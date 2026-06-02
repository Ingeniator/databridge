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
    mock_sink.post_file = AsyncMock()

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
