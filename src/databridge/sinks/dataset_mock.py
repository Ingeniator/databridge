from __future__ import annotations

import json
import re

import httpx

from databridge.config import DatasinkConfig
from databridge.sinks.base import BaseSink

_TIMEOUT = 30.0
_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


class DatasetMockSink(BaseSink):
    def __init__(self, config: DatasinkConfig) -> None:
        super().__init__(config)
        self._url = config.url.rstrip("/")
        self._token: str | None = None
        self._dataset_ids: dict[str, str] = {}  # name → uuid

    async def _get_token(self) -> str:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{self._url}/realms/test/protocol/openid-connect/token",
                data={"grant_type": "client_credentials", "client_id": "any", "client_secret": "any"},
            )
            r.raise_for_status()
            return r.json()["access_token"]

    async def _auth_headers(self) -> dict:
        if not self._token:
            self._token = await self._get_token()
        return {"Authorization": f"Bearer {self._token}"}

    async def ping(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{self._url}/health")
            r.raise_for_status()

    async def list_datasets(self) -> list[dict[str, str]]:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{self._url}/_mock/datasets")
            r.raise_for_status()
            return [{"name": d["name"], "uid": d["id"]} for d in r.json().get("datasets", []) if d.get("id")]

    async def create_dataset(self, name_or_id: str) -> None:
        if _UUID_RE.match(name_or_id):
            self._dataset_ids[name_or_id] = name_or_id
            self.external_id = name_or_id
            return
        headers = await self._auth_headers()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{self._url}/api/v0/datasets",
                json={"name": name_or_id},
                headers=headers,
            )
            if r.status_code == 409:
                datasets = await self.list_datasets()
                match = next((d for d in datasets if d["name"] == name_or_id), None)
                if match:
                    self._dataset_ids[name_or_id] = match["uid"]
                    self.external_id = match["uid"]
                return
            r.raise_for_status()
            dataset_id = r.json()["id"]
            self._dataset_ids[name_or_id] = dataset_id
            self.external_id = dataset_id

    async def post_file(
        self,
        dataset: str,
        record: dict,
        filename: str | None = None,
    ) -> str:
        if dataset not in self._dataset_ids:
            await self.create_dataset(dataset)
        dataset_id = self._dataset_ids[dataset]
        headers = await self._auth_headers()
        fname = filename or "record.json"
        content = json.dumps(record).encode()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(
                f"{self._url}/api/v0/datasets/{dataset_id}/files",
                files={"file": (fname, content, "application/json")},
                headers=headers,
            )
            r.raise_for_status()
        return record.get("source_url", "")

    async def finalise(self) -> None:
        pass
