from __future__ import annotations

import asyncio

import arq
from arq.connections import RedisSettings

from databridge.config import get_settings
from databridge.export.worker import run_export_job
from databridge.logging_config import setup_logging


async def startup(ctx: dict) -> None:
    from databridge.db.pool import create_pool
    from databridge.export.sweep import run_sweep_loop
    from prometheus_client import start_http_server

    settings = get_settings()
    if settings.export.worker_metrics_port > 0:
        start_http_server(settings.export.worker_metrics_port)
    pool = await create_pool()
    ctx["pool"] = pool
    ctx["settings"] = settings
    ctx["_sweep_task"] = asyncio.ensure_future(run_sweep_loop(pool, settings.export))


async def shutdown(ctx: dict) -> None:
    if task := ctx.get("_sweep_task"):
        task.cancel()
    if pool := ctx.get("pool"):
        await pool.close()


class WorkerSettings:
    functions = [run_export_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().export.redis_url)


if __name__ == "__main__":
    settings = get_settings()
    setup_logging(debug=settings.server.debug, silence_probes=settings.server.silence_probes)
    arq.run_worker(WorkerSettings)
