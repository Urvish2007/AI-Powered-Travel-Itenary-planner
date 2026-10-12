"""structlog configuration shared by the API, workers and scripts."""

from __future__ import annotations

import logging
import sys
from typing import Any, cast

import structlog

_configured = False


def configure_logging(*, level: str = "INFO", json_output: bool = False) -> None:
    """Configure structlog + stdlib logging once per process."""
    global _configured
    if _configured:
        return

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    # These libraries are chatty at INFO and drown out application logs.
    for noisy in ("pymongo", "motor", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            # add_logger_name reads ``logger.name``, so it requires the stdlib
            # logger factory below - pairing it with PrintLoggerFactory raises.
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger, configuring logging on first use."""
    if not _configured:
        configure_logging()
    # structlog ships no annotation for get_logger, so the bound type has to be
    # stated here rather than inferred.
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
