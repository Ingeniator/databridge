"""T059 — E2E: ping a user connection and the system source."""
from playwright.sync_api import expect


def test_ping_user_connection(page, api_conn):
    page.goto("http://localhost:5010")

    ping_btn = page.get_by_test_id(f"conn-ping-btn-{api_conn}")
    expect(ping_btn).to_be_visible()
    ping_btn.click()

    status = page.locator(f"#conn-status-{api_conn}")
    expect(status).not_to_have_text("…", timeout=10000)
    assert status.inner_text() in ("reachable", "unreachable")


def test_ping_system_source(page):
    page.goto("http://localhost:5010")

    sys_section = page.get_by_test_id("system-sources-section")
    expect(sys_section).to_be_visible()

    sys_card = page.locator("[id^='sys-card-']").first
    sys_id = sys_card.get_attribute("id").replace("sys-card-", "")

    page.get_by_test_id(f"sys-ping-btn-{sys_id}").click()

    status = page.locator(f"#sys-status-{sys_id}")
    expect(status).not_to_have_text("…", timeout=10000)
    assert status.inner_text() in ("reachable", "unreachable")

    # System source cards must not have edit or delete buttons
    assert page.locator(f"#conn-edit-btn-{sys_id}").count() == 0
    assert page.locator(f"#conn-delete-btn-{sys_id}").count() == 0
