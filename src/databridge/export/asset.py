from __future__ import annotations

import re

import httpx

from databridge.sinks.base import BaseSink

_URL_FIELD_NAMES = {
    "url", "file_url", "image_url", "asset_url", "media_url",
    "thumbnail_url", "download_url",
}
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


class AssetResolutionError(Exception):
    pass


def detect_asset_url_fields(
    schema: dict[str, dict],
    sample_records: list[dict],
) -> list[str]:
    candidates: list[str] = []
    for field_path, field_info in schema.items():
        leaf = field_path.rsplit(".", 1)[-1]
        if leaf.lower() in _URL_FIELD_NAMES:
            candidates.append(field_path)
            continue
        example = field_info.get("example")
        if example and isinstance(example, str) and _URL_RE.match(example):
            candidates.append(field_path)
            continue
        # Check sample values
        for record in sample_records:
            val = record.get(field_path) or record.get(leaf)
            if val and isinstance(val, str) and _URL_RE.match(val):
                candidates.append(field_path)
                break
    return candidates


async def resolve_assets(
    record: dict,
    url_fields: list[str],
    url_prefix: str,
    asset_sink: BaseSink,
    asset_dataset: str,
) -> dict:
    updated = dict(record)
    for field in url_fields:
        raw_value = updated.get(field)
        if not raw_value:
            continue
        url = (url_prefix + str(raw_value)) if url_prefix else str(raw_value)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(url)
                if r.status_code >= 400:
                    raise AssetResolutionError(f"HTTP {r.status_code} fetching {url}")
                content = r.content
        except httpx.RequestError as exc:
            raise AssetResolutionError(f"Request error fetching {url}: {exc}") from exc

        filename = url.rstrip("/").rsplit("/", 1)[-1] or "asset"
        ref = await asset_sink.post_file(asset_dataset, {"data": content.hex(), "source_url": url}, filename)
        updated[field] = ref or filename

    return updated
