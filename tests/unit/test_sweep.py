"""Unit tests for export/sweep.py."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from databridge.config import ExportSettings
from databridge.export.sweep import mark_stale_jobs, run_sweep_loop, ttl_purge_jobs


def _pool(execute_return: str = "UPDATE 0") -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock(return_value=execute_return)
    return pool


# ---------------------------------------------------------------------------
# mark_stale_jobs
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_mark_stale_jobs_no_rows_affected():
    pool = _pool("UPDATE 0")
    await mark_stale_jobs(pool, timeout_minutes=15)
    pool.execute.assert_awaited_once()


@pytest.mark.anyio
async def test_mark_stale_jobs_rows_affected():
    pool = _pool("UPDATE 3")
    await mark_stale_jobs(pool, timeout_minutes=15)
    pool.execute.assert_awaited_once()
    call_args = pool.execute.call_args
    # timeout value forwarded as string parameter
    assert "15" in call_args.args


@pytest.mark.anyio
async def test_mark_stale_jobs_passes_timeout_in_error_message():
    pool = _pool("UPDATE 1")
    await mark_stale_jobs(pool, timeout_minutes=30)
    msg_arg = pool.execute.call_args.args[1]
    assert "30" in msg_arg


# ---------------------------------------------------------------------------
# ttl_purge_jobs
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ttl_purge_jobs_no_rows_deleted():
    pool = _pool("DELETE 0")
    await ttl_purge_jobs(pool, ttl_days=7)
    pool.execute.assert_awaited_once()


@pytest.mark.anyio
async def test_ttl_purge_jobs_rows_deleted():
    pool = _pool("DELETE 5")
    await ttl_purge_jobs(pool, ttl_days=7)
    pool.execute.assert_awaited_once()
    call_args = pool.execute.call_args
    assert "7" in call_args.args


# ---------------------------------------------------------------------------
# run_sweep_loop
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_run_sweep_loop_calls_both_functions_then_sleeps(monkeypatch):
    pool = _pool("UPDATE 0")
    pool.execute = AsyncMock(return_value="UPDATE 0")
    settings = ExportSettings(stale_job_timeout_minutes=5, job_ttl_days=3)

    sleep_calls = []

    async def fake_sleep(secs):
        sleep_calls.append(secs)
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_sweep_loop(pool, settings)

    assert sleep_calls == [60]
    assert pool.execute.await_count == 2  # mark_stale + ttl_purge


@pytest.mark.anyio
async def test_run_sweep_loop_continues_after_exception(monkeypatch):
    pool = MagicMock()
    # First tick raises, second tick succeeds, third tick cancels via sleep
    pool.execute = AsyncMock(side_effect=[Exception("db error"), "UPDATE 0", "DELETE 0"])
    settings = ExportSettings(stale_job_timeout_minutes=5, job_ttl_days=3)

    tick = 0

    async def fake_sleep(secs):
        nonlocal tick
        tick += 1
        if tick >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_sweep_loop(pool, settings)

    # loop ran twice: once with error (caught), once clean
    assert tick == 2
