from __future__ import annotations

import asyncio

import asyncpg
import structlog

from databridge.config import ExportSettings

logger = structlog.get_logger(__name__)


async def mark_stale_jobs(pool: asyncpg.Pool, timeout_minutes: int) -> None:
    result = await pool.execute(
        """
        UPDATE export_jobs
        SET status = 'failed',
            error_message = $1,
            completed_at = NOW()
        WHERE status = 'running'
          AND last_heartbeat_at < NOW() - ($2 || ' minutes')::INTERVAL
        """,
        f"job timed out — worker did not respond within {timeout_minutes} minutes",
        str(timeout_minutes),
    )
    if result != "UPDATE 0":
        logger.info("stale_jobs_marked_failed", result=result)


async def ttl_purge_jobs(pool: asyncpg.Pool, ttl_days: int) -> None:
    result = await pool.execute(
        """
        DELETE FROM export_jobs
        WHERE status IN ('completed', 'failed')
          AND completed_at < NOW() - ($1 || ' days')::INTERVAL
        """,
        str(ttl_days),
    )
    if result != "DELETE 0":
        logger.info("old_jobs_purged", result=result)


async def run_sweep_loop(pool: asyncpg.Pool, settings: ExportSettings) -> None:
    while True:
        try:
            await mark_stale_jobs(pool, settings.stale_job_timeout_minutes)
            await ttl_purge_jobs(pool, settings.job_ttl_days)
        except Exception:
            logger.error("sweep_loop_error", exc_info=True)
        await asyncio.sleep(60)
