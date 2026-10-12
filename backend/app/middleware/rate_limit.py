"""Fixed-window rate limiting.

Uses Redis when it is configured, so the limit holds across every replica, and
falls back to an in-process counter when it is not. The in-process path is
honest about its limitation: with N replicas the effective limit is N times the
configured one, which is fine for local development and wrong for production.
Enable Redis before relying on this for anything real.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.observability.logging import get_logger

logger = get_logger(__name__)

# Probes and metrics are scraped on a schedule and must never be throttled.
# The health routes live under the API prefix (``/api/v1/health``), so the
# exemption is derived from settings rather than hard-coded: a bare ``/health``
# would never match anything the app actually serves.
EXEMPT_PATHS = frozenset(
    {
        "/",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        f"{settings.api_v1_prefix}/health",
        f"{settings.api_v1_prefix}/health/ready",
    }
)


class _LocalWindow:
    """In-process fixed-window counter, keyed by client."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._last_sweep = time.monotonic()

    def hit(self, key: str, limit: int, window: int) -> tuple[bool, int, float]:
        now = time.monotonic()
        self._maybe_sweep(now, window)

        timestamps = self._hits[key]
        cutoff = now - window
        # Drop expired entries in place, so memory tracks active clients only.
        timestamps[:] = [t for t in timestamps if t > cutoff]

        if len(timestamps) >= limit:
            retry_after = max(0.0, timestamps[0] + window - now)
            return False, 0, retry_after

        timestamps.append(now)
        return True, limit - len(timestamps), 0.0

    def _maybe_sweep(self, now: float, window: int) -> None:
        """Periodically evict clients that have gone quiet."""
        if now - self._last_sweep < window:
            return
        cutoff = now - window
        for key in list(self._hits):
            if not self._hits[key] or self._hits[key][-1] <= cutoff:
                del self._hits[key]
        self._last_sweep = now


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit: int | None = None, window: int | None = None) -> None:
        super().__init__(app)
        self.limit = limit or settings.rate_limit_requests
        self.window = window or settings.rate_limit_window_seconds
        self._local = _LocalWindow()
        # `redis` is an optional dependency, imported lazily in `_check_redis`,
        # so the client cannot be named in an annotation here.
        self._redis: Any | None = None
        self._redis_failed = False

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        key = self._key(request)
        allowed, remaining, retry_after = await self._check(key)

        if not allowed:
            logger.info("rate_limited", key=key, path=request.url.path)
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many requests. Please slow down.",
                        "details": {"retry_after_seconds": round(retry_after, 1)},
                    }
                },
                headers={
                    "Retry-After": str(max(1, int(retry_after))),
                    "X-RateLimit-Limit": str(self.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

    def _key(self, request: Request) -> str:
        """Rate limit per authenticated user where possible, else per address.

        Keying on the token prefix rather than the IP stops one office NAT from
        throttling everyone behind it.
        """
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            return f"token:{authorization[7:][:32]}"

        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"
        return f"ip:{request.client.host if request.client else 'unknown'}"

    async def _check(self, key: str) -> tuple[bool, int, float]:
        if settings.redis_enabled and not self._redis_failed:
            result = await self._check_redis(key)
            if result is not None:
                return result
        return self._local.hit(key, self.limit, self.window)

    async def _check_redis(self, key: str) -> tuple[bool, int, float] | None:
        try:
            client = self._redis
            if client is None:
                import redis.asyncio as aioredis

                client = aioredis.from_url(str(settings.redis_uri), decode_responses=True)
                self._redis = client

            redis_key = f"ratelimit:{key}"
            # INCR then EXPIRE on first hit gives a fixed window with one round trip
            # for every request after the first.
            pipeline = client.pipeline()
            pipeline.incr(redis_key)
            pipeline.ttl(redis_key)
            count, ttl = await pipeline.execute()

            if ttl < 0:
                await client.expire(redis_key, self.window)
                ttl = self.window

            if count > self.limit:
                return False, 0, float(ttl)
            return True, self.limit - int(count), 0.0
        except Exception as exc:
            logger.warning(
                "redis_rate_limit_failed", error=str(exc), falling_back_to="in_process"
            )
            self._redis_failed = True
            return None
