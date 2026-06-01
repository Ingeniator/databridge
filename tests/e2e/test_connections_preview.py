"""T060 — E2E: preview data via the system source (has seeded data)."""
from playwright.sync_api import expect


def test_preview_system_source(page):
    """Use the local-clickhouse system source which has 20 seeded rows."""
    page.goto("http://localhost:5010")

    sys_card = page.locator("[id^='sys-card-']").first
    sys_id = sys_card.get_attribute("id").replace("sys-card-", "")

    page.get_by_test_id(f"sys-preview-btn-{sys_id}").click()

    # Panel becomes active — submit button should be enabled
    submit = page.get_by_test_id("preview-submit-btn")
    expect(submit).to_be_enabled(timeout=3000)
    submit.click()

    # Table should appear with rows
    table = page.get_by_test_id("preview-table")
    expect(table).to_be_visible(timeout=10000)
    rows = page.locator("#preview-tbody tr")
    expect(rows.first).to_be_visible(timeout=5000)
    assert rows.count() >= 1
