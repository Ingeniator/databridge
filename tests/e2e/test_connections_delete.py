"""T061 — E2E: delete a connection and verify empty state appears."""
import requests
from playwright.sync_api import expect


def test_delete_connection_shows_empty_state(page, api_conn):
    page.goto("http://localhost:5010")

    card = page.locator(f"#conn-card-{api_conn}")
    expect(card).to_be_visible()

    page.once("dialog", lambda d: d.accept())
    page.get_by_test_id(f"conn-delete-btn-{api_conn}").click()

    expect(card).to_be_hidden(timeout=5000)

    # If that was the last connection, empty state should be visible
    remaining = page.locator("[id^='conn-card-']").count()
    if remaining == 0:
        expect(page.get_by_test_id("empty-state")).to_be_visible()
