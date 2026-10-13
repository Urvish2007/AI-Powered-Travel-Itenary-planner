"""Celery application.

Itinerary generation is the one genuinely slow operation in this service: a
real LLM round trip plus a multi-second solve. The HTTP path runs it inline
because that keeps the API honest for small trips, but anything longer - bulk
re-planning, embedding backfills, scheduled maintenance - belongs here.

Celery workers are separate processes, so each task opens its own MongoDB
connection and closes it again; there is no shared event loop with the API.
"""

from __future__ import annotations

import asyncio
from typing import Any

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

from app.core.config import settings
from app.observability.logging import configure_logging, get_logger

logger = get_logger(__name__)

celery_app = Celery(
    "itinerary_planner",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Prefetching one task at a time keeps long solves from queueing behind each
    # other on a single worker.
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    task_time_limit=600,
    task_soft_time_limit=540,
    result_expires=86_400,
    task_always_eager=settings.celery_task_always_eager,
    # Fail fast when no broker is listening. Without these, publishing a task
    # retries with backoff and the HTTP request that triggered it hangs for
    # minutes; the API would rather return 503 immediately.
    broker_connection_retry_on_startup=False,
    broker_connection_max_retries=0,
    broker_transport_options={
        "max_retries": 0,
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
    },
    beat_schedule={
        "purge-expired-refresh-tokens": {
            "task": "app.workers.tasks.purge_expired_tokens",
            "schedule": 3600.0,
        },
        "backfill-place-embeddings": {
            "task": "app.workers.tasks.backfill_embeddings",
            "schedule": 21_600.0,
        },
    },
)

# One event loop per worker process, reused across tasks. Creating a fresh loop
# per task would also discard the Motor connection pool each time.
_loop: asyncio.AbstractEventLoop | None = None


@worker_process_init.connect
def _init_worker(**_: Any) -> None:
    global _loop
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

    from app.db.mongodb import connect_to_mongo

    _loop.run_until_complete(connect_to_mongo())
    logger.info("celery_worker_ready")


@worker_process_shutdown.connect
def _shutdown_worker(**_: Any) -> None:
    global _loop
    if _loop is None:
        return
    from app.db.mongodb import close_mongo_connection

    _loop.run_until_complete(close_mongo_connection())
    _loop.close()
    _loop = None
    logger.info("celery_worker_stopped")


def run_async(coro: Any) -> Any:
    """Run a coroutine on the worker's loop.

    Falls back to ``asyncio.run`` when no worker loop exists, which is the case
    under ``task_always_eager`` in tests.
    """
    if _loop is not None and not _loop.is_closed():
        return _loop.run_until_complete(coro)
    return asyncio.run(coro)
