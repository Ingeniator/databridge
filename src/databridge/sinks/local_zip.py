from __future__ import annotations

import hashlib
import json
import zipfile
from io import BytesIO
from pathlib import Path
from string import Formatter

from databridge.config import DatasinkConfig
from databridge.sinks.base import BaseSink


class LocalZipSink(BaseSink):
    def __init__(self, config: DatasinkConfig) -> None:
        super().__init__(config)
        self._path = Path(config.path)
        self._template = config.filename_template
        self._buffers: dict[str, tuple[BytesIO, zipfile.ZipFile]] = {}
        self._job_id: str = ""

    def _resolve_filename(self, record: dict) -> str:
        if not self._template:
            blob = json.dumps(record, sort_keys=True).encode()
            return hashlib.sha256(blob).hexdigest()[:16] + ".json"
        try:
            field_names = [fn for _, fn, _, _ in Formatter().parse(self._template) if fn]
            values = {fn: str(record.get(fn, "")) for fn in field_names}
            return self._template.format(**values)
        except (KeyError, ValueError):
            blob = json.dumps(record, sort_keys=True).encode()
            return hashlib.sha256(blob).hexdigest()[:16] + ".json"

    async def ping(self) -> None:
        self._path.mkdir(parents=True, exist_ok=True)
        test_file = self._path / ".write_test"
        try:
            test_file.touch()
            test_file.unlink()
        except OSError as exc:
            raise OSError(f"Path not writable: {self._path}") from exc

    async def list_datasets(self) -> list[dict[str, str]]:
        if not self._path.exists():
            return []
        return [{"name": p.stem, "uid": p.stem} for p in self._path.glob("*.zip")]

    async def create_dataset(self, name: str) -> None:
        self._path.mkdir(parents=True, exist_ok=True)
        buf = BytesIO()
        zf = zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED)
        self._buffers[name] = (buf, zf)

    async def post_file(
        self,
        dataset: str,
        record: dict,
        filename: str | None = None,
    ) -> str:
        if dataset not in self._buffers:
            await self.create_dataset(dataset)
        _, zf = self._buffers[dataset]
        fname = filename or self._resolve_filename(record)
        raw = record.get("data")
        if raw is not None and isinstance(raw, str):
            zf.writestr(fname, bytes.fromhex(raw))
        else:
            zf.writestr(fname, json.dumps(record))
        return f"{dataset}/{fname}"

    async def finalise(self) -> None:
        for name, (buf, zf) in self._buffers.items():
            zf.close()
            suffix = f"_{self._job_id}" if self._job_id else ""
            dest = self._path / f"{name}{suffix}.zip"
            dest.write_bytes(buf.getvalue())
        self._buffers.clear()
