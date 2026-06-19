from __future__ import annotations

import json
from pathlib import Path
from typing import IO

from databridge.config import DatasinkConfig
from databridge.sinks.base import BaseSink


class LocalJsonlSink(BaseSink):
    def __init__(self, config: DatasinkConfig) -> None:
        super().__init__(config)
        self._path = Path(config.path)
        self._handles: dict[str, IO[str]] = {}
        self._job_id: str = ""
        self.records_skipped: int = 0

    async def ping(self) -> None:
        self._path.mkdir(parents=True, exist_ok=True)
        test_file = self._path / ".write_test"
        try:
            test_file.touch()
            test_file.unlink()
        except OSError as exc:
            raise OSError(f"Path not writable: {self._path}") from exc

    async def list_datasets(self) -> list[str]:
        if not self._path.exists():
            return []
        return [p.stem for p in self._path.glob("*.jsonl")]

    async def create_dataset(self, name: str) -> None:
        self._path.mkdir(parents=True, exist_ok=True)
        suffix = f"_{self._job_id}" if self._job_id else ""
        dest = self._path / f"{name}{suffix}.jsonl"
        self._handles[name] = dest.open("w", encoding="utf-8")

    async def post_file(
        self,
        dataset: str,
        record: dict,
        filename: str | None = None,
    ) -> str:
        raw = record.get("data")
        if raw is not None and isinstance(raw, str):
            suffix = f"_{self._job_id}" if self._job_id else ""
            asset_dir = self._path / f"{dataset}{suffix}"
            asset_dir.mkdir(parents=True, exist_ok=True)
            fname = filename or "asset"
            (asset_dir / fname).write_bytes(bytes.fromhex(raw))
            return f"{dataset}{suffix}/{fname}"
        if dataset not in self._handles:
            await self.create_dataset(dataset)
        try:
            line = json.dumps(record)
        except (TypeError, ValueError):
            self.records_skipped += 1
            return ""
        self._handles[dataset].write(line + "\n")
        return ""

    async def finalise(self) -> None:
        for fh in self._handles.values():
            fh.flush()
            fh.close()
        self._handles.clear()
