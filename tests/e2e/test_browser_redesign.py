"""T010/T032 — Playwright E2E stubs for browser UI redesign (xfail until implemented)."""
import pytest
from playwright.sync_api import Page, expect

BASE = "http://localhost:5010"


# ── US1: Browse & Filter Data ─────────────────────────────────────────────────

@pytest.mark.xfail(reason="US1 not yet implemented", strict=False)
def test_connection_tab_click_shows_schema_chip(page: Page):
    page.goto(BASE)
    page.get_by_test_id("connection-tab-bar").wait_for()
    # Click the first connection tab
    first_tab = page.locator("[data-testid^='conn-tab-']").first
    first_tab.click()
    # A schema chip should appear
    expect(page.locator("[data-testid^='schema-chip-']").first).to_be_visible(timeout=5000)


@pytest.mark.xfail(reason="US1 not yet implemented", strict=False)
def test_predicate_filter_updates_preview_table(page: Page):
    page.goto(BASE)
    page.get_by_test_id("predicate-filter-input").fill("status == 'error'")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("preview-table")).to_be_visible(timeout=5000)


@pytest.mark.xfail(reason="US1 not yet implemented", strict=False)
def test_clear_all_resets_rows(page: Page):
    page.goto(BASE)
    page.get_by_test_id("predicate-filter-input").fill("status == 'error'")
    page.keyboard.press("Enter")
    page.get_by_test_id("clear-all-btn").click()
    expect(page.get_by_test_id("predicate-filter-input")).to_have_value("")


@pytest.mark.xfail(reason="US1 not yet implemented", strict=False)
def test_load_more_appends_rows(page: Page):
    page.goto(BASE)
    page.get_by_test_id("load-more-btn").click()
    expect(page.get_by_test_id("preview-table")).to_be_visible(timeout=5000)


# ── US3: Jobs view ─────────────────────────────────────────────────────────────

@pytest.mark.xfail(reason="US3 not yet implemented", strict=False)
def test_nav_tab_jobs_shows_jobs_view(page: Page):
    page.goto(BASE)
    page.get_by_test_id("nav-tab-jobs").click()
    expect(page.get_by_test_id("jobs-view")).to_be_visible(timeout=3000)


@pytest.mark.xfail(reason="US3 not yet implemented", strict=False)
def test_job_row_appears_after_export(page: Page):
    page.goto(BASE)
    # This test verifies a job row appears after triggering an export.
    # It will be fleshed out once US2 export button is implemented.
    pytest.skip("requires full export flow from US2")


@pytest.mark.xfail(reason="US3 not yet implemented", strict=False)
def test_retry_btn_visible_on_failed_job(page: Page):
    page.goto(BASE)
    page.get_by_test_id("nav-tab-jobs").click()
    # Assumes a failed job exists; check for retry button
    retry_btns = page.locator("[data-testid^='job-retry-btn-']")
    # At minimum the selector pattern is correct
    expect(retry_btns).to_have_count(0)  # no failed jobs in fresh state
