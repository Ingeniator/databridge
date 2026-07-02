from __future__ import annotations

import json
from io import StringIO

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError

from databridge.config import DatasinkConfig
from databridge.sinks.base import BaseSink


class S3JsonlSink(BaseSink):
    def __init__(self, config: DatasinkConfig) -> None:
        super().__init__(config)
        self._bucket = config.bucket
        self._prefix = config.key_prefix.rstrip("/") if config.key_prefix else ""
        self._region = config.region or "us-east-1"
        self._access_key_id = config.access_key_id or None
        self._secret_access_key = config.secret_access_key or None
        self._endpoint = config.endpoint or None
        self._buffers: dict[str, StringIO] = {}
        self._asset_buffers: dict[str, bytes] = {}
        self._job_id: str = ""
        self.records_skipped: int = 0

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
            kwargs["config"] = Config(
                s3={"addressing_style": "path"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            )
        return kwargs

    def _make_key(self, *parts: str) -> str:
        segments = ([self._prefix] if self._prefix else []) + list(parts)
        return "/".join(segments)

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
        self._buffers[name] = StringIO()

    async def post_file(
        self,
        dataset: str,
        record: dict,
        filename: str | None = None,
    ) -> str:
        raw = record.get("data")
        if raw is not None and isinstance(raw, str):
            suffix = f"_{self._job_id}" if self._job_id else ""
            fname = filename or "asset"
            key = self._make_key(f"{dataset}{suffix}", fname)
            self._asset_buffers[key] = bytes.fromhex(raw)
            return key
        if dataset not in self._buffers:
            await self.create_dataset(dataset)
        try:
            line = json.dumps(record)
        except (TypeError, ValueError):
            self.records_skipped += 1
            return ""
        self._buffers[dataset].write(line + "\n")
        return ""

    async def finalise(self) -> None:
        async with self._session().client("s3", **self._client_kwargs()) as s3:
            for name, buf in self._buffers.items():
                suffix = f"_{self._job_id}" if self._job_id else ""
                key = self._make_key(f"{name}{suffix}.jsonl")
                await s3.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=buf.getvalue().encode(),
                    ContentType="application/x-ndjson",
                )
            for key, data in self._asset_buffers.items():
                await s3.put_object(Bucket=self._bucket, Key=key, Body=data)
        self._buffers.clear()
        self._asset_buffers.clear()
