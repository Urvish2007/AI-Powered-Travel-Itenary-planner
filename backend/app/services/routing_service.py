"""Measured travel matrices from a road-routing provider, with a safe fallback.

The optimiser plans on a :class:`TravelMatrix`. By default that is the
straight-line estimate, which needs nothing and never fails. Setting
``ROUTING_PROVIDER`` swaps in distances and durations measured on the real
network, fetched here in one matrix call for all the points of a request.

Every way this can go wrong ends in the estimate rather than an error: no
provider configured, too many points, a mode the provider cannot route, the
provider down or rate-limiting, a malformed reply, or an unroutable pair. The
matrix says which it was (``estimated`` / ``fallback_reason``), so a response
can tell the traveller its figures are estimates instead of implying otherwise.

Resilience, in order of application:

* a TTL cache, keyed on provider, profile and rounded coordinates, so a
  comparison followed by an apply does not pay for the same matrix twice;
* a circuit breaker, so a provider that keeps failing is left alone for a
  cooldown instead of adding its timeout to every request;
* a per-attempt timeout and bounded retries with exponential backoff, for
  timeouts, connection errors, 429 and 5xx only. Other 4xx (a bad key, an
  over-limit request) are not retried: they will not fix themselves.

Logs carry the provider, profile, point count, latency and outcome - never
coordinates (they locate a traveller's hotel) or the API key.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

import httpx
import numpy as np

from app.core.config import RoutingProviderName, settings
from app.models.enums import TransportMode
from app.observability.logging import get_logger
from app.optimization.distance import (
    MODE_OVERHEAD_MINUTES,
    Coordinate,
    TravelMatrix,
    estimated_matrix,
)

logger = get_logger(__name__)

# Coordinates are rounded to ~1 m for the cache key: closer than that is the
# same doorway, and unrounded floats would make every key unique.
_CACHE_PRECISION = 5

_CacheKey = tuple[str, str, tuple[tuple[float, float], ...]]


class RoutingError(Exception):
    """A provider could not produce a usable matrix. Never escapes this module."""

    def __init__(self, reason: str, *, retryable: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class RawMatrix:
    """What a provider returns: metres and seconds, possibly with gaps."""

    meters: list[list[float | None]]
    seconds: list[list[float | None]]


class RoutingProvider(Protocol):
    """A road-routing backend. One call prices every pair of ``coords``."""

    name: str

    def profile_for(self, mode: TransportMode) -> str | None:
        """The provider's profile for ``mode``, or ``None`` if it cannot route it."""
        ...

    async def fetch(
        self, client: httpx.AsyncClient, coords: list[Coordinate], profile: str
    ) -> RawMatrix: ...


def _check_status(response: httpx.Response) -> None:
    if response.status_code == 429 or response.status_code >= 500:
        raise RoutingError(f"http_{response.status_code}", retryable=True)
    if response.status_code >= 400:
        raise RoutingError(f"http_{response.status_code}")


def _matrix_field(body: Any, key: str) -> list[list[float | None]]:
    value = body.get(key) if isinstance(body, dict) else None
    if not isinstance(value, list) or not all(isinstance(row, list) for row in value):
        raise RoutingError("malformed_response")
    return value


class OpenRouteService:
    """openrouteservice.org's Matrix API (or a self-hosted ORS instance).

    No transit profile exists, so transit trips keep the estimate.
    """

    name = "openrouteservice"
    _PROFILES: ClassVar[dict[TransportMode, str]] = {
        TransportMode.WALKING: "foot-walking",
        TransportMode.CYCLING: "cycling-regular",
        TransportMode.DRIVING: "driving-car",
    }

    def __init__(self, base_url: str, api_key: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def profile_for(self, mode: TransportMode) -> str | None:
        return self._PROFILES.get(mode)

    async def fetch(
        self, client: httpx.AsyncClient, coords: list[Coordinate], profile: str
    ) -> RawMatrix:
        headers = {"Authorization": self.api_key} if self.api_key else {}
        response = await client.post(
            f"{self.base_url}/v2/matrix/{profile}",
            headers=headers,
            json={
                # GeoJSON order: longitude first.
                "locations": [[c.lng, c.lat] for c in coords],
                "metrics": ["distance", "duration"],
                "units": "m",
            },
        )
        _check_status(response)
        body = response.json()
        return RawMatrix(_matrix_field(body, "distances"), _matrix_field(body, "durations"))


class Osrm:
    """An OSRM server's table service.

    Point this at a self-hosted instance: the public demo server routes cars
    only and its usage policy rules out production traffic.
    """

    name = "osrm"
    _PROFILES: ClassVar[dict[TransportMode, str]] = {
        TransportMode.WALKING: "foot",
        TransportMode.CYCLING: "bike",
        TransportMode.DRIVING: "driving",
    }

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def profile_for(self, mode: TransportMode) -> str | None:
        return self._PROFILES.get(mode)

    async def fetch(
        self, client: httpx.AsyncClient, coords: list[Coordinate], profile: str
    ) -> RawMatrix:
        points = ";".join(f"{c.lng:.6f},{c.lat:.6f}" for c in coords)
        response = await client.get(
            f"{self.base_url}/table/v1/{profile}/{points}",
            params={"annotations": "distance,duration"},
        )
        _check_status(response)
        body = response.json()
        if not isinstance(body, dict) or body.get("code") != "Ok":
            raise RoutingError("provider_error")
        return RawMatrix(_matrix_field(body, "distances"), _matrix_field(body, "durations"))


# ---------------------------------------------------------------------------
# Cache and circuit breaker (per process)
# ---------------------------------------------------------------------------


class _TTLCache:
    """A small LRU with expiry. Per process, which is all a matrix needs."""

    def __init__(self) -> None:
        self._items: OrderedDict[_CacheKey, tuple[float, TravelMatrix]] = OrderedDict()

    def get(self, key: _CacheKey) -> TravelMatrix | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires, matrix = item
        if expires < time.monotonic():
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return matrix

    def put(self, key: _CacheKey, matrix: TravelMatrix) -> None:
        if settings.routing_cache_ttl_seconds <= 0:
            return
        self._items[key] = (time.monotonic() + settings.routing_cache_ttl_seconds, matrix)
        self._items.move_to_end(key)
        while len(self._items) > settings.routing_cache_max_entries:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()


class _CircuitBreaker:
    """Opens after N consecutive failures; lets one attempt through per cooldown."""

    def __init__(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def allows(self) -> bool:
        return time.monotonic() >= self.open_until

    def succeeded(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def failed(self) -> None:
        self.failures += 1
        if self.failures >= settings.routing_breaker_threshold:
            self.open_until = time.monotonic() + settings.routing_breaker_cooldown_seconds

    def reset(self) -> None:
        self.succeeded()


_cache = _TTLCache()
_breaker = _CircuitBreaker()

# Tests swap the network out by assigning an ``httpx.MockTransport`` here.
transport: httpx.AsyncBaseTransport | None = None


def reset_state() -> None:
    """Forget cached matrices and breaker state. For tests and settings changes."""
    _cache.clear()
    _breaker.reset()


def configured_provider() -> RoutingProvider | None:
    """The provider ``ROUTING_PROVIDER`` names, or ``None`` for the estimate."""
    name = settings.routing_provider
    if name is RoutingProviderName.OPENROUTESERVICE:
        key = settings.routing_api_key
        return OpenRouteService(
            settings.routing_base_url or "https://api.openrouteservice.org",
            key.get_secret_value() if key else None,
        )
    if name is RoutingProviderName.OSRM:
        if not settings.routing_base_url:
            # Refusing to guess here is deliberate: the only public default is
            # the demo server, which must not take production traffic.
            return None
        return Osrm(settings.routing_base_url)
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def travel_matrix(
    coords: list[Coordinate],
    mode: TransportMode,
    provider: RoutingProvider | None = None,
) -> TravelMatrix:
    """Distances and durations between every pair of ``coords``.

    Measured when a provider is configured and able; the straight-line estimate
    otherwise, with the reason recorded. Never raises for provider trouble.
    """
    provider = provider or configured_provider()

    def fallback(reason: str | None) -> TravelMatrix:
        return estimated_matrix(coords, mode, fallback_reason=reason)

    if provider is None:
        reason = (
            "osrm_base_url_missing"
            if settings.routing_provider is RoutingProviderName.OSRM
            else None
        )
        return fallback(reason)
    if len(coords) < 2:
        return fallback(None)
    if len(coords) > settings.routing_max_locations:
        return fallback("too_many_locations")
    profile = provider.profile_for(mode)
    if profile is None:
        return fallback("mode_not_supported")

    points = tuple(
        (round(c.lat, _CACHE_PRECISION), round(c.lng, _CACHE_PRECISION)) for c in coords
    )
    key: _CacheKey = (provider.name, profile, points)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    if not _breaker.allows():
        return fallback("circuit_open")

    started = time.perf_counter()
    try:
        raw = await _fetch_with_retry(provider, coords, profile)
        matrix = _to_travel_matrix(raw, len(coords), mode, provider.name)
    except RoutingError as exc:
        _breaker.failed()
        logger.warning(
            "routing_fallback",
            provider=provider.name,
            profile=profile,
            locations=len(coords),
            reason=exc.reason,
            breaker_open=not _breaker.allows(),
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )
        return fallback(exc.reason)

    _breaker.succeeded()
    _cache.put(key, matrix)
    logger.info(
        "routing_matrix_fetched",
        provider=provider.name,
        profile=profile,
        locations=len(coords),
        elapsed_ms=round((time.perf_counter() - started) * 1000),
    )
    return matrix


async def _fetch_with_retry(
    provider: RoutingProvider, coords: list[Coordinate], profile: str
) -> RawMatrix:
    attempts = 1 + max(0, settings.routing_max_retries)
    timeout = httpx.Timeout(settings.routing_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        for attempt in range(attempts):
            try:
                return await provider.fetch(client, coords, profile)
            except httpx.TimeoutException:
                error = RoutingError("timeout", retryable=True)
            except httpx.TransportError:
                error = RoutingError("connection_error", retryable=True)
            except ValueError:  # the body was not JSON
                error = RoutingError("malformed_response")
            except RoutingError as exc:
                error = exc
            if not error.retryable or attempt == attempts - 1:
                raise error
            await asyncio.sleep(settings.routing_retry_backoff_seconds * 2**attempt)
    raise RoutingError("no_attempts")  # pragma: no cover - attempts is at least 1


def _to_travel_matrix(
    raw: RawMatrix, size: int, mode: TransportMode, provider: str
) -> TravelMatrix:
    """Validate a provider's reply and convert it to kilometres and minutes.

    The whole matrix is rejected on any gap or bad value: a matrix that is
    partly measured and partly guessed would still be labelled measured.
    """
    if len(raw.meters) != size or len(raw.seconds) != size:
        raise RoutingError("malformed_response")
    try:
        meters = np.array(raw.meters, dtype=float)
        seconds = np.array(raw.seconds, dtype=float)
    except (TypeError, ValueError) as exc:
        raise RoutingError("malformed_response") from exc
    if meters.shape != (size, size) or seconds.shape != (size, size):
        raise RoutingError("malformed_response")
    # ``None`` becomes NaN above; ORS uses null for a pair it cannot connect.
    if not (np.isfinite(meters).all() and np.isfinite(seconds).all()):
        raise RoutingError("unroutable_pair")
    if (meters < 0).any() or (seconds < 0).any():
        raise RoutingError("malformed_response")

    # The provider measures moving time; the per-mode overhead (finding the
    # entrance, parking) is added so measured and estimated legs mean the same.
    overhead = MODE_OVERHEAD_MINUTES.get(mode, 0)
    minutes = np.ceil(seconds / 60.0) + overhead
    np.fill_diagonal(minutes, 0.0)
    distance_km = meters / 1000.0
    np.fill_diagonal(distance_km, 0.0)
    return TravelMatrix(
        distance_km=distance_km,
        duration_minutes=minutes.astype(int),
        estimated=False,
        provider=provider,
    )
