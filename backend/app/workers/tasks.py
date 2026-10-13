"""Celery tasks."""

from __future__ import annotations

from typing import Any

from celery import shared_task

from app.observability.logging import get_logger
from app.workers.celery_app import run_async

logger = get_logger(__name__)


@shared_task(
    name="app.workers.tasks.generate_itinerary",
    bind=True,
    max_retries=2,
    default_retry_delay=20,
)
def generate_itinerary_task(
    self: Any, trip_id: str, brief: str = "", regenerate: bool = True
) -> dict[str, Any]:
    """Generate an itinerary off the request path.

    Retries on transient failure, but never on an infeasible plan: no amount of
    retrying makes a schedule fit that the constraints forbid.
    """
    from app.core.exceptions import OptimizationError
    from app.services import itinerary_service, trip_service

    async def _run() -> dict[str, Any]:
        trip = await trip_service.get_trip(trip_id)
        itinerary = await itinerary_service.generate_itinerary(
            trip, brief=brief, regenerate=regenerate
        )
        return {
            "itinerary_id": str(itinerary.id),
            "trip_id": trip_id,
            "version": itinerary.version,
            "days": len(itinerary.days),
            "total_distance_km": itinerary.total_distance_km,
            "solver_status": itinerary.optimization.solver_status,
        }

    try:
        return run_async(_run())
    except OptimizationError as exc:
        logger.warning("task_infeasible", trip_id=trip_id, error=exc.message)
        return {"trip_id": trip_id, "error": exc.message, "retryable": False}
    except Exception as exc:
        logger.exception("task_failed", trip_id=trip_id, error=str(exc))
        raise self.retry(exc=exc) from exc


@shared_task(name="app.workers.tasks.backfill_embeddings")
def backfill_embeddings() -> dict[str, int]:
    """Embed any place that is missing an embedding."""
    from app.services.place_service import backfill_embeddings as backfill

    written = run_async(backfill())
    return {"embedded": written}


@shared_task(name="app.workers.tasks.purge_expired_tokens")
def purge_expired_tokens() -> dict[str, int]:
    """Delete refresh tokens past their expiry.

    MongoDB's TTL index already does this; the task exists so the cleanup can be
    forced and observed on a schedule rather than only implicitly.
    """
    from app.services.auth_service import purge_expired_tokens as purge

    deleted = run_async(purge())
    return {"deleted": deleted}


@shared_task(name="app.workers.tasks.reoptimize_trip")
def reoptimize_trip(trip_id: str) -> dict[str, Any]:
    """Re-run planning for one trip, producing a new version."""
    return generate_itinerary_task(trip_id, brief="", regenerate=True)
