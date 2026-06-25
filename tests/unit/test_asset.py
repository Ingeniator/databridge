"""Unit test stubs — detect_asset_url_fields and resolve_assets."""
import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock


def test_detect_returns_image_url_by_name():
    from databridge.export.asset import detect_asset_url_fields
    schema = {"image_url": {"type": "string", "example": "something"}}
    result = detect_asset_url_fields(schema, [])
    assert "image_url" in result


def test_detect_returns_field_by_url_example():
    from databridge.export.asset import detect_asset_url_fields
    schema = {"some_field": {"type": "string", "example": "https://cdn.example.com/img.png"}}
    result = detect_asset_url_fields(schema, [])
    assert "some_field" in result


def test_detect_does_not_return_non_url_fields():
    from databridge.export.asset import detect_asset_url_fields
    schema = {"name": {"type": "string", "example": "Alice"}, "count": {"type": "int", "example": 42}}
    result = detect_asset_url_fields(schema, [])
    assert "name" not in result
    assert "count" not in result


@pytest.mark.asyncio
async def test_resolve_assets_with_prefix():
    from databridge.export.asset import resolve_assets

    mock_sink = MagicMock()
    mock_sink.post_file = AsyncMock(return_value=None)

    with respx.mock:
        respx.get("https://cdn.example.com/img.png").mock(
            return_value=httpx.Response(200, content=b"binary_content")
        )
        result = await resolve_assets(
            record={"image_url": "img.png"},
            url_fields=["image_url"],
            url_prefix="https://cdn.example.com/",
            asset_sink=mock_sink,
            asset_dataset="assets_ds",
        )
    mock_sink.post_file.assert_called_once()
    assert result["image_url"] == "img.png"  # replaced with stored asset filename


@pytest.mark.asyncio
async def test_resolve_assets_returns_updated_record():
    from databridge.export.asset import resolve_assets

    mock_sink = MagicMock()
    mock_sink.post_file = AsyncMock()

    with respx.mock:
        respx.get("https://cdn.example.com/photo.jpg").mock(
            return_value=httpx.Response(200, content=b"photo_data")
        )
        result = await resolve_assets(
            record={"file_url": "https://cdn.example.com/photo.jpg"},
            url_fields=["file_url"],
            url_prefix="",
            asset_sink=mock_sink,
            asset_dataset="assets",
        )
    assert "file_url" in result
    mock_sink.post_file.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_assets_raises_on_404():
    from databridge.export.asset import resolve_assets, AssetResolutionError

    mock_sink = MagicMock()
    mock_sink.post_file = AsyncMock()

    with respx.mock:
        respx.get("https://cdn.example.com/missing.jpg").mock(
            return_value=httpx.Response(404)
        )
        with pytest.raises(AssetResolutionError):
            await resolve_assets(
                record={"image_url": "https://cdn.example.com/missing.jpg"},
                url_fields=["image_url"],
                url_prefix="",
                asset_sink=mock_sink,
                asset_dataset="assets",
            )


def test_resolve_assets_prefix_empty_uses_field_value_as_is():
    """prefix='' means field value used as-is."""
    from databridge.export.asset import detect_asset_url_fields
    schema = {"download_url": {"type": "string", "example": "https://files.example.com/doc.pdf"}}
    result = detect_asset_url_fields(schema, [])
    assert "download_url" in result


# --- S3 media link tests ---

def test_detect_media_url_field_by_name():
    from databridge.export.asset import detect_asset_url_fields
    schema = {"media_url": {"type": "string", "example": "https://my-bucket.s3.amazonaws.com/videos/clip.mp4"}}
    result = detect_asset_url_fields(schema, [])
    assert "media_url" in result


def test_detect_s3_url_from_sample_record():
    from databridge.export.asset import detect_asset_url_fields
    schema = {"attachment": {"type": "string"}}
    records = [{"attachment": "https://my-bucket.s3.amazonaws.com/files/report.pdf"}]
    result = detect_asset_url_fields(schema, records)
    assert "attachment" in result


@pytest.mark.asyncio
async def test_resolve_assets_s3_media_url():
    from databridge.export.asset import resolve_assets

    s3_url = "https://my-bucket.s3.amazonaws.com/media/video.mp4"
    mock_sink = MagicMock()
    mock_sink.post_file = AsyncMock(return_value=None)

    with respx.mock:
        respx.get(s3_url).mock(return_value=httpx.Response(200, content=b"\x00\x01\x02\x03"))
        result = await resolve_assets(
            record={"id": "rec-1", "media_url": s3_url},
            url_fields=["media_url"],
            url_prefix="",
            asset_sink=mock_sink,
            asset_dataset="media_assets",
        )

    assert result["id"] == "rec-1"
    assert result["media_url"] == "video.mp4"
    mock_sink.post_file.assert_called_once_with(
        "media_assets",
        {"data": "00010203", "source_url": s3_url},
        "video.mp4",
    )


@pytest.mark.asyncio
async def test_resolve_assets_s3_presigned_url_with_query_params():
    from databridge.export.asset import resolve_assets

    # Pre-signed S3 URL with query parameters — filename is extracted before '?'
    s3_presigned = (
        "https://my-bucket.s3.amazonaws.com/uploads/photo.jpg"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=3600"
    )
    mock_sink = MagicMock()
    mock_sink.post_file = AsyncMock(return_value=None)

    with respx.mock:
        respx.get(s3_presigned).mock(return_value=httpx.Response(200, content=b"jpeg_data"))
        result = await resolve_assets(
            record={"media_url": s3_presigned},
            url_fields=["media_url"],
            url_prefix="",
            asset_sink=mock_sink,
            asset_dataset="media_assets",
        )

    # The stored filename comes from the last path segment (before query string)
    stored_filename = mock_sink.post_file.call_args[0][2]
    assert "photo.jpg" in stored_filename
    assert result["media_url"] == stored_filename
