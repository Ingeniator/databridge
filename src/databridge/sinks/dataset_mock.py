from __future__ import annotations

import httpx

from databridge.config import DatasinkConfig
from databridge.sinks.base import BaseSink

_TIMEOUT = 30.0


class DatasetMockSink(BaseSink):
    def __init__(self, config: DatasinkConfig) -> None:
        super().__init__(config)
        self._url = config.url.rstrip("/")

    async def ping(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{self._url}/health")
            r.raise_for_status()

    async def list_datasets(self) -> list[str]:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{self._url}/datasets")
            r.raise_for_status()
            return r.json().get("datasets", [])

    async def create_dataset(self, name: str) -> None:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(f"{self._url}/datasets", json={"name": name})
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
            r = await client.post(f"{self._url}/datasets/{dataset}/files", json=record)
            r.raise_for_status()
        return record.get("source_url", "")

    async def finalise(self) -> None:
        pass
