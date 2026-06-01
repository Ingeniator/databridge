"""T058 — E2E: add a ClickHouse connection via the browser UI."""
import requests
import pytest
from playwright.sync_api import expect

_BASE = "http://localhost:5010"
_AUTH = {"X-Group-ID": "e2e-user"}


def test_add_clickhouse_connection(page):
    page.goto(_BASE)
    page.get_by_test_id("add-connection-btn").click()

    page.get_by_test_id("conn-label-input").fill("E2E ClickHouse")
    page.get_by_test_id("conn-type-select").select_option("clickhouse")
    page.get_by_test_id("conn-role-select").select_option("source")
    page.get_by_test_id("conn-url-input").fill("http://localhost:8123")
    page.locator("#cred_user").fill("default")
    page.locator("#cred_password").fill("")

    page.get_by_test_id("conn-submit-btn").click()

    # Card should appear in the list
    card = page.locator("[id^='conn-card-']").first
    expect(card).to_be_visible(timeout=5000)

    # Clean up — delete via API
    conn_id = card.get_attribute("id").replace("conn-card-", "")
    requests.delete(f"{_BASE}/api/v1/connections/{conn_id}", headers=_AUTH, timeout=5)
