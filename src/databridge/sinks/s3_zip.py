from __future__ import annotations

import hashlib
import json
import zipfile
from io import BytesIO
from string import Formatter

import aioboto3
from botocore.exceptions import ClientError

from databridge.config import DatasinkConfig
from databridge.sinks.base import BaseSink


class S3ZipSink(BaseSink):
    def __init__(self, config: DatasinkConfig) -> None:
        super().__init__(config)
        self._bucket = config.bucket
        self._prefix = config.key_prefix.rstrip("/") if config.key_prefix else ""
        self._region = config.region or "us-east-1"
        self._access_key_id = config.access_key_id or None
        self._secret_access_key = config.secret_access_key or None
        self._endpoint = config.endpoint or None
        self._template = config.filename_template
        self._buffers: dict[str, tuple[BytesIO, zipfile.ZipFile]] = {}
        self._job_id: str = ""

    def _session(self) -> aioboto3.Session:
        return aioboto3.Session(
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
            region_name=self._region,
        )

    def _client_kwargs(self) -> dict:
        kwargs: dict = {}
        if self._endpoint:
            kwargs["endpoint_url"] = self._endpoint
        return kwargs

    def _make_key(self, *parts: str) -> str:
        segments = ([self._prefix] if self._prefix else []) + list(parts)
        return "/".join(segments)

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
        async with self._session().client("s3", **self._client_kwargs()) as s3:
            try:
                await s3.head_bucket(Bucket=self._bucket)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("404", "NoSuchBucket"):
                    kwargs: dict = {}
                    if self._region != "us-east-1":
                        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self._region}
                    await s3.create_bucket(Bucket=self._bucket, **kwargs)
                else:
                    raise OSError(f"S3 bucket not accessible: {self._bucket}") from exc
            except Exception as exc:
                raise OSError(f"S3 bucket not accessible: {self._bucket}") from exc

    async def list_datasets(self) -> list[dict[str, str]]:
        prefix = f"{self._prefix}/" if self._prefix else ""
        seen: set[str] = set()
        result: list[dict[str, str]] = []
        async with self._session().client("s3", **self._client_kwargs()) as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key: str = obj["Key"]
                    name = key.removeprefix(prefix).split("/")[0].split(".")[0]
                    if name and name not in seen:
                        seen.add(name)
                        result.append({"name": name, "uid": name})
        return result

    async def create_dataset(self, name: str) -> None:
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
        async with self._session().client("s3", **self._client_kwargs()) as s3:
            for name, (buf, zf) in self._buffers.items():
                zf.close()
                suffix = f"_{self._job_id}" if self._job_id else ""
                key = self._make_key(f"{name}{suffix}.zip")
                await s3.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=buf.getvalue(),
                    ContentType="application/zip",
                )
        self._buffers.clear()
