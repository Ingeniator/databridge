from __future__ import annotations

from abc import ABC, abstractmethod

from databridge.config import DatasinkConfig


class BaseSink(ABC):
    def __init__(self, config: DatasinkConfig) -> None:
        self._config = config

    @abstractmethod
    async def ping(self) -> None: ...

    @abstractmethod
    async def list_datasets(self) -> list[str]: ...

    @abstractmethod
    async def create_dataset(self, name: str) -> None: ...

    @abstractmethod
    async def post_file(
        self,
        dataset: str,
        record: dict,
        filename: str | None = None,
    ) -> None: ...

    @abstractmethod
    async def finalise(self) -> None: ...
