"""Per-request identity, timing and log binding."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.observability.logging import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
PROCESS_TIME_HEADER = "X-Process-Time"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id, time the request, and bind both to the log context.

    The id is taken from an inbound ``X-Request-ID`` when present so that a trace
    started at the edge stays correlated through this service, and generated
    otherwise. Every log line emitted while handling the request carries it.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request_failed",
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
            raise

        elapsed = time.perf_counter() - started
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[PROCESS_TIME_HEADER] = f"{elapsed:.4f}"

        # Successful reads are noise at INFO; anything slow or failing is not.
        if response.status_code >= 400 or elapsed > 1.0:
            logger.info(
                "request_completed",
                status_code=response.status_code,
                elapsed_ms=int(elapsed * 1000),
            )
        return response
