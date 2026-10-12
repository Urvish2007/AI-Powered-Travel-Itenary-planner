"""Prometheus metrics and the middleware that records them."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests handled.",
    ["method", "path", "status"],
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
ITINERARIES_GENERATED = Counter(
    "itineraries_generated_total",
    "Itineraries produced by the planning pipeline.",
    ["source"],
)
OPTIMIZER_RUNS = Counter(
    "optimizer_runs_total",
    "OR-Tools optimisation runs.",
    ["status"],
)
OPTIMIZER_DURATION = Histogram(
    "optimizer_duration_seconds",
    "Time spent inside the OR-Tools solver.",
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)
LLM_CALLS = Counter(
    "llm_calls_total",
    "Calls issued to an LLM provider.",
    ["provider", "status"],
)
LLM_TOKENS = Counter(
    "llm_tokens_total",
    "Tokens consumed per provider.",
    ["provider", "kind"],
)
DB_UP = Gauge("mongodb_up", "1 when the last MongoDB ping succeeded, else 0.")


def metrics_response() -> Response:
    """Render the Prometheus exposition payload."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Record request counts and latency, labelled by route template.

    The route *template* (``/api/v1/trips/{trip_id}``) is used rather than the raw
    path so that per-id URLs do not explode metric cardinality.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            status_code = 500
            raise
        finally:
            elapsed = time.perf_counter() - start
            path = _route_template(request)
            HTTP_REQUESTS.labels(request.method, path, str(status_code)).inc()
            HTTP_LATENCY.labels(request.method, path).observe(elapsed)
        return response


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", request.url.path)
