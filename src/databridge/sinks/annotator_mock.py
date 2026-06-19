from __future__ import annotations

import httpx

from databridge.config import DatasinkConfig
from databridge.sinks.base import BaseSink

_TIMEOUT = 30.0


class AnnotatorMockSink(BaseSink):
    def __init__(self, config: DatasinkConfig) -> None:
        super().__init__(config)
        self._url = config.url.rstrip("/")

    async def ping(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{self._url}/health")
            r.raise_for_status()

    async def list_datasets(self) -> list[str]:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{self._url}/api/v1/projects")
            r.raise_for_status()
            projects = r.json()
            if isinstance(projects, list):
                return [p.get("name", "") for p in projects if p.get("name")]
            return projects.get("items", [])

    async def create_dataset(self, name: str) -> None:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(f"{self._url}/api/v1/projects", json={"name": name})
            if r.status_code == 409:
                return
            r.raise_for_status()

    async def post_file(
        self,
        dataset: str,
        record: dict,
        filename: str | None = None,
    ) -> str:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(f"{self._url}/api/v1/projects/{dataset}/tasks", json=record)
            r.raise_for_status()
        return record.get("source_url", "")

    async def finalise(self) -> None:
        pass
