from __future__ import annotations

from abc import ABC, abstractmethod

from databridge.config import DatasinkConfig


class BaseSink(ABC):
    def __init__(self, config: DatasinkConfig) -> None:
        self._config = config
        self.external_id: str | None = None

    @abstractmethod
    async def ping(self) -> None: ...

    @abstractmethod
    async def list_datasets(self) -> list[dict[str, str]]: ...

    @abstractmethod
    async def create_dataset(self, name: str) -> None: ...

    @abstractmethod
    async def post_file(
        self,
        dataset: str,
        record: dict,
        filename: str | None = None,
    ) -> str:
        """Store one record/file and return the reference to embed in export data.

        For local sinks returns a relative path like ``"{dataset}/{filename}"``.
        For service sinks returns the original source URL so downstream systems
        can fetch it directly.
        """
        ...

    @abstractmethod
    async def finalise(self) -> None: ...
