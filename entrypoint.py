"""Main application entrypoint.

Configures logging, creates FastAPI app, and starts the server.
"""

import os
import resource
import signal
import sys

from databridge.config import get_settings
from databridge.logging_config import setup_logging

settings = get_settings()
setup_logging(debug=settings.server.debug, silence_probes=settings.server.silence_probes)

import structlog  # noqa: E402

logger = structlog.get_logger(__name__)

from databridge.main import app  # noqa: E402, F401


def _log_worker_info():
    """Log resource usage for the current worker process."""
    pid = os.getpid()
    ppid = os.getppid()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    if sys.platform == "linux":
        rss_mb = usage.ru_maxrss / 1024
    else:
        rss_mb = usage.ru_maxrss / (1024 * 1024)
    logger.info(
        "worker_started",
        pid=pid,
        ppid=ppid,
        rss_mb=round(rss_mb, 1),
    )


def _on_signal(sig, frame):
    """Log when a worker receives a termination signal."""
    sig_name = signal.Signals(sig).name
    logger.warning("worker_signal_received", pid=os.getpid(), signal=sig_name)
    sys.exit(128 + sig)


for _sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(_sig, _on_signal)

_log_worker_info()


if __name__ == "__main__":
    import uvicorn

    logger.info(
        "Starting server",
        host=settings.server.host,
        port=settings.server.port,
        workers=settings.server.workers,
        pid=os.getpid(),
    )
    uvicorn.run(
        "entrypoint:app",
        workers=settings.server.workers,
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.server.debug,
        log_level="debug" if settings.server.debug else "info",
    )
