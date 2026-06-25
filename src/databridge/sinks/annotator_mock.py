from __future__ import annotations

import json
import re

import httpx

from databridge.config import DatasinkConfig
from databridge.sinks.base import BaseSink

_TIMEOUT = 30.0
_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


class AnnotatorMockSink(BaseSink):
    def __init__(self, config: DatasinkConfig) -> None:
        super().__init__(config)
        self._url = config.url.rstrip("/")
        self._job_id: str = ""
        self._project_id: str | None = None
        self._dataset_id: str | None = None
        self._pool_id: str | None = None

    async def _get_pool_id(self, client: httpx.AsyncClient) -> str:
        if self._pool_id is None:
            r = await client.get(f"{self._url}/api/v0/pools/hardcoded")
            r.raise_for_status()
            self._pool_id = r.json()["pool_id"]
        return self._pool_id

    async def ping(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{self._url}/health")
            r.raise_for_status()

    async def list_datasets(self) -> list[dict[str, str]]:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{self._url}/api/v0/markup_project")
            r.raise_for_status()
            return [
                {"name": p["name"], "uid": p["uid"]}
                for p in r.json().get("items", [])
                if p.get("name") and p.get("uid")
            ]

    async def create_dataset(self, project_id_or_name: str) -> None:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{self._url}/api/v0/markup_project")
            r.raise_for_status()
            projects = r.json().get("items", [])

            if _UUID_RE.match(project_id_or_name):
                project = next((p for p in projects if p.get("uid") == project_id_or_name), None)
                if project is None:
                    raise RuntimeError(f"markup project {project_id_or_name!r} not found")
                self._project_id = project["uid"]
                project_name = project["name"]
            else:
                project = next((p for p in projects if p.get("name") == project_id_or_name), None)
                if project is None:
                    r = await client.post(
                        f"{self._url}/api/v0/markup_project", json={"name": project_id_or_name}
                    )
                    r.raise_for_status()
                    project = r.json()
                self._project_id = project["uid"]
                project_name = project_id_or_name

            dataset_name = f"{project_name}-{self._job_id[:8]}" if self._job_id else project_name
            r = await client.post(f"{self._url}/api/v0/datasets", json={"name": dataset_name})
            r.raise_for_status()
            self._dataset_id = r.json()["id"]

    async def post_file(
        self,
        dataset: str,
        record: dict,
        filename: str | None = None,
    ) -> str:
        if self._dataset_id is None:
            raise RuntimeError("create_dataset must be called before post_file")
        fname = filename or "record.json"
        content = json.dumps(record).encode()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{self._url}/api/v0/datasets/{self._dataset_id}/files",
                files={"file": (fname, content, "application/json")},
            )
            r.raise_for_status()
        return record.get("source_url", "")

    async def finalise(self) -> None:
        if self._project_id is None or self._dataset_id is None:
            return
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            pool_id = await self._get_pool_id(client)

            r = await client.post(
                f"{self._url}/api/v0/markup_project/{self._project_id}/pools/{pool_id}",
            )
            if r.status_code not in (200, 204):
                r.raise_for_status()

            r = await client.post(
                f"{self._url}/api/v0/tasks",
                json={"project_id": self._project_id, "dataset_id": self._dataset_id},
            )
            r.raise_for_status()
            task_id = r.json()["uid"]

            r = await client.post(f"{self._url}/api/v0/tasks/{task_id}/start")
            r.raise_for_status()

            self.external_id = task_id
