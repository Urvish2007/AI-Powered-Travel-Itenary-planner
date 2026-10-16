"""Liveness, readiness and diagnostic endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app import __version__
from app.core.config import settings
from app.core.deps import AdminUser
from app.db import mongodb
from app.schemas.common import HealthStatus

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthStatus,
    summary="Liveness probe",
    description="Returns 200 whenever the process is running. Does not touch the database.",
)
async def health() -> HealthStatus:
    return HealthStatus(
        status="ok",
        version=__version__,
        environment=str(settings.environment),
        mongodb="not_checked",
    )


@router.get(
    "/health/ready",
    response_model=HealthStatus,
    summary="Readiness probe",
    description=(
        "Pings MongoDB. Returns 503 when the database is unreachable, so an "
        "orchestrator stops routing traffic to this instance."
    ),
    responses={503: {"description": "A dependency is unavailable."}},
)
async def readiness(response: Response) -> HealthStatus:
    database_ok = await mongodb.ping()
    if not database_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthStatus(
        status="ok" if database_ok else "degraded",
        version=__version__,
        environment=str(settings.environment),
        mongodb="up" if database_ok else "down",
        checks={"mongodb": database_ok},
    )


@router.get(
    "/health/details",
    summary="Detailed diagnostics (admin only)",
    description="Server build info, collection index inventory and AI configuration.",
)
async def details(admin: AdminUser) -> dict:
    from app.ai.graph import LANGGRAPH_AVAILABLE
    from app.db.indexes import index_report
    from app.optimization.engine import ORTOOLS_AVAILABLE

    return {
        "version": __version__,
        "environment": str(settings.environment),
        "mongodb": {
            "reachable": await mongodb.ping(),
            "database": settings.mongodb_db_name,
            "server": await mongodb.server_info(),
            "indexes": await index_report(),
            "vector_search_enabled": settings.mongodb_vector_search_enabled,
        },
        "ai": {
            "provider": str(settings.llm_provider),
            "model": settings.resolved_llm_model,
            "embedding_dimensions": settings.embedding_dimensions,
            "embeddings": await _embedding_coverage(),
            "langgraph_available": LANGGRAPH_AVAILABLE,
        },
        "optimization": {
            "ortools_available": ORTOOLS_AVAILABLE,
            "time_limit_seconds": settings.optimizer_time_limit_seconds,
        },
    }


async def _embedding_coverage() -> dict[str, object]:
    """How much of the corpus semantic search can currently see."""
    from app.ai.embeddings import EmbeddingUnavailableError, current_space

    try:
        space = current_space().key
    except EmbeddingUnavailableError as exc:
        return {"space": None, "error": exc.message}
    places = mongodb.get_database()["places"]
    return {
        "space": space,
        "embedded": await places.count_documents({"embedding_space": space}),
        "total": await places.count_documents({}),
    }
