"""FastAPI application factory and lifespan wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app import __version__
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.db.indexes import ensure_vector_search_index
from app.db.mongodb import close_mongo_connection, connect_to_mongo
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.observability.logging import configure_logging, get_logger
from app.observability.metrics import PrometheusMiddleware, metrics_response
from app.observability.tracing import setup_sentry, setup_tracing

logger = get_logger(__name__)

DESCRIPTION = """
Backend for an AI itinerary planner.

**How a plan is built**

1. A free-text brief is parsed into structured intent by an LLM.
2. Candidate places are recalled two ways - a `2dsphere` radius query and a
   vector-similarity search - and unioned.
3. Each candidate is scored for this traveller, blending the model's judgement
   with objective signals like rating and popularity.
4. **OR-Tools** schedules them as a vehicle-routing problem with time windows,
   where one vehicle is one day of the trip. Opening hours, visit durations,
   pace and daily distance are all hard constraints.
5. The finished schedule is narrated, then validated.

Every generated itinerary is a new version, and every run is recorded under
`/ai/runs` so a plan can be explained after the fact.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open and close the resources the app owns for its whole lifetime."""
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    setup_sentry()

    await connect_to_mongo()
    await ensure_vector_search_index()

    logger.info(
        "application_started",
        environment=str(settings.environment),
        version=__version__,
        llm_provider=str(settings.llm_provider),
    )
    try:
        yield
    finally:
        await close_mongo_connection()
        logger.info("application_stopped")


def create_app() -> FastAPI:
    """Build the FastAPI application.

    A factory rather than a module-level singleton, so tests can construct an
    app with different settings without re-importing the module.
    """
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        # Hide the interactive docs in production; the schema stays available to
        # internal tooling that knows the URL.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url="/openapi.json",
        swagger_ui_parameters={"defaultModelsExpandDepth": 0},
    )

    # Order matters: the outermost middleware is added last, so request context
    # is established before metrics and rate limiting observe the request.
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Process-Time"],
    )
    if settings.rate_limit_enabled:
        app.add_middleware(RateLimitMiddleware)
    if settings.metrics_enabled:
        app.add_middleware(PrometheusMiddleware)
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    setup_tracing(app)

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    if not settings.is_production:

        @app.get("/", include_in_schema=False)
        async def root() -> RedirectResponse:
            return RedirectResponse(url="/docs")

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> JSONResponse:
        """Prometheus scrape endpoint."""
        if not settings.metrics_enabled:
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "code": "not_found",
                        "message": "Metrics are disabled.",
                    }
                },
            )
        return metrics_response()  # type: ignore[return-value]

    return app


app = create_app()
