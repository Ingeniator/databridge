"""E2E stubs — asset resolution UI flow backed by MinIO (docker-compose.dev.yml).

MinIO seeds three files into the test-media bucket on startup:
  http://localhost:9100/test-media/clip.mp4
  http://localhost:9100/test-media/photo.jpg
  http://localhost:9100/test-media/audio.mp3

The ClickHouse `media_events` table has records whose media_url and
thumbnail_url columns point to those files, giving us a real asset
resolution target without hitting external CDNs.
"""
import pytest
import requests
from playwright.sync_api import Page, expect

BASE = "http://localhost:5010"
AUTH = {"X-Group-ID": "e2e-user"}


# ── Sanity: MinIO reachable ───────────────────────────────────────────────────

def test_minio_bucket_reachable(s3_media_base_url):
    """Fail fast if the MinIO container / seed hasn't run yet."""
    r = requests.get(f"{s3_media_base_url}/clip.mp4", timeout=5)
    assert r.status_code == 200, (
        f"MinIO test-media bucket unreachable at {s3_media_base_url}. "
        "Run: docker compose -f docker-compose.dev.yml up minio minio-seed"
    )


def test_minio_all_seed_files_present(s3_media_base_url):
    for filename in ("clip.mp4", "photo.jpg", "audio.mp3"):
        r = requests.get(f"{s3_media_base_url}/{filename}", timeout=5)
        assert r.status_code == 200, f"Missing seed file: {filename}"


# ── Asset field detection ─────────────────────────────────────────────────────

@pytest.mark.xfail(reason="media_events source not yet wired into the browser UI", strict=False)
def test_media_url_field_auto_detected_in_browser(page: Page):
    """media_url column in media_events should surface as a detected asset URL field."""
    page.goto(BASE)
    page.get_by_test_id("connection-tab-bar").wait_for()
    page.locator("[data-testid='conn-tab-media_events']").click()
    expect(page.locator("[data-testid='asset-fields-chip-media_url']")).to_be_visible(timeout=5000)


# ── Export panel: asset resolution toggle ─────────────────────────────────────

@pytest.mark.xfail(reason="export panel UI not yet implemented", strict=False)
def test_asset_resolution_toggle_shows_url_fields_picker(page: Page):
    page.goto(BASE)
    page.get_by_test_id("nav-tab-export").click()
    page.get_by_test_id("asset-resolution-toggle").click()
    expect(page.get_by_test_id("asset-url-fields-picker")).to_be_visible(timeout=3000)


@pytest.mark.xfail(reason="export panel UI not yet implemented", strict=False)
def test_asset_url_prefix_input_accepts_s3_endpoint(page: Page, s3_media_base_url):
    page.goto(BASE)
    page.get_by_test_id("nav-tab-export").click()
    page.get_by_test_id("asset-resolution-toggle").click()
    page.get_by_test_id("asset-url-prefix-input").fill(s3_media_base_url + "/")
    expect(page.get_by_test_id("asset-url-prefix-input")).to_have_value(s3_media_base_url + "/")


# ── API-level asset resolution test endpoint ──────────────────────────────────

def test_asset_resolution_api_resolves_s3_clip(s3_media_base_url):
    """POST /api/v1/export-jobs/test-asset-resolution fetches clip.mp4 from MinIO."""
    resp = requests.post(
        f"{BASE}/api/v1/export-jobs/test-asset-resolution",
        json={
            "url_fields": ["media_url"],
            "url_prefix": "",
            "record": {"id": "item-001", "media_url": f"{s3_media_base_url}/clip.mp4"},
        },
        headers=AUTH,
        timeout=10,
    )
    # 404 means the endpoint isn't wired yet — skip rather than hard-fail
    if resp.status_code == 404:
        pytest.skip("test-asset-resolution endpoint not yet implemented")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["results"][0]["ok"] is True
