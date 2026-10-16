"""Road-routing provider: fallback, resilience, and engine parity.

No network: every provider call goes through an ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import date

import httpx
import numpy as np
import pytest
from pydantic import SecretStr

from app.core.config import OptimizerAlgorithm, RoutingProviderName, settings
from app.models.enums import TransportMode
from app.optimization import algorithms
from app.optimization.distance import (
    MODE_OVERHEAD_MINUTES,
    Coordinate,
    TravelMatrix,
    build_travel_matrix,
)
from app.optimization.engine import compare_algorithms, optimize
from app.optimization.schemas import DayConfig, OptimizationPlace, OptimizationRequest
from app.services import routing_service

POINTS = [
    Coordinate(41.3870, 2.1701),
    Coordinate(41.4036, 2.1744),
    Coordinate(41.3809, 2.1228),
]


def _square(value: float | None, size: int = 3) -> list[list[float | None]]:
    return [[0.0 if i == j else value for j in range(size)] for i in range(size)]


def _ors_body(meters: float | None = 2500.0, seconds: float | None = 1800.0) -> dict:
    return {"distances": _square(meters), "durations": _square(seconds)}


@pytest.fixture(autouse=True)
def routing(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    """ORS configured, no backoff, clean cache and breaker; yields seen requests."""
    monkeypatch.setattr(settings, "routing_provider", RoutingProviderName.OPENROUTESERVICE)
    monkeypatch.setattr(settings, "routing_api_key", None)
    monkeypatch.setattr(settings, "routing_base_url", "https://ors.test")
    monkeypatch.setattr(settings, "routing_retry_backoff_seconds", 0.0)
    monkeypatch.setattr(settings, "routing_max_retries", 2)
    monkeypatch.setattr(settings, "routing_breaker_threshold", 3)
    routing_service.reset_state()
    seen: list[httpx.Request] = []
    yield seen
    routing_service.transport = None
    routing_service.reset_state()


def _serve(
    seen: list[httpx.Request], *responses: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Answer successive requests with ``responses``; the last one repeats."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[min(len(seen) - 1, len(responses) - 1)](request)

    routing_service.transport = httpx.MockTransport(handler)


def ok(body: object) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _r: httpx.Response(200, json=body)


def status(code: int) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _r: httpx.Response(code, json={"error": "nope"})


async def _matrix(mode: TransportMode = TransportMode.WALKING) -> TravelMatrix:
    return await routing_service.travel_matrix(POINTS, mode)


# ---------------------------------------------------------------------------
# The default and the refusals
# ---------------------------------------------------------------------------


async def test_default_provider_is_the_estimate(
    monkeypatch: pytest.MonkeyPatch, routing: list[httpx.Request]
) -> None:
    monkeypatch.setattr(settings, "routing_provider", RoutingProviderName.HAVERSINE)
    matrix = await _matrix()

    distances, durations = build_travel_matrix(POINTS, TransportMode.WALKING)
    assert matrix.estimated is True
    assert matrix.provider == "haversine"
    assert matrix.fallback_reason is None
    assert np.array_equal(matrix.distance_km, distances)
    assert np.array_equal(matrix.duration_minutes, durations)
    assert routing == []


async def test_osrm_without_a_base_url_is_refused_not_guessed(
    monkeypatch: pytest.MonkeyPatch, routing: list[httpx.Request]
) -> None:
    monkeypatch.setattr(settings, "routing_provider", RoutingProviderName.OSRM)
    monkeypatch.setattr(settings, "routing_base_url", None)
    matrix = await _matrix()
    assert matrix.estimated is True
    assert matrix.fallback_reason == "osrm_base_url_missing"
    assert routing == []


async def test_too_many_points_skip_the_provider(
    monkeypatch: pytest.MonkeyPatch, routing: list[httpx.Request]
) -> None:
    monkeypatch.setattr(settings, "routing_max_locations", 2)
    matrix = await _matrix()
    assert matrix.fallback_reason == "too_many_locations"
    assert routing == []


async def test_a_mode_the_provider_cannot_route_keeps_the_estimate(
    routing: list[httpx.Request],
) -> None:
    matrix = await _matrix(TransportMode.TRANSIT)
    assert matrix.fallback_reason == "mode_not_supported"
    assert routing == []


# ---------------------------------------------------------------------------
# A measured matrix
# ---------------------------------------------------------------------------


async def test_openrouteservice_request_and_conversion(
    monkeypatch: pytest.MonkeyPatch, routing: list[httpx.Request]
) -> None:
    monkeypatch.setattr(settings, "routing_api_key", SecretStr("secret-key"))
    _serve(routing, ok(_ors_body(meters=2500.0, seconds=1801.0)))

    matrix = await _matrix()

    [request] = routing
    assert request.method == "POST"
    assert request.url == "https://ors.test/v2/matrix/foot-walking"
    assert request.headers["Authorization"] == "secret-key"
    body = json.loads(request.content)
    # GeoJSON order: longitude first.
    assert body["locations"][0] == [POINTS[0].lng, POINTS[0].lat]

    assert matrix.estimated is False
    assert matrix.provider == "openrouteservice"
    assert matrix.distance_km[0][1] == pytest.approx(2.5)
    # 1801 s rounds up to 31 minutes, plus the walking overhead.
    assert (
        matrix.duration_minutes[0][1] == 31 + MODE_OVERHEAD_MINUTES[TransportMode.WALKING]
    )
    assert matrix.duration_minutes[1][1] == 0


async def test_osrm_table_request(
    monkeypatch: pytest.MonkeyPatch, routing: list[httpx.Request]
) -> None:
    monkeypatch.setattr(settings, "routing_provider", RoutingProviderName.OSRM)
    monkeypatch.setattr(settings, "routing_base_url", "https://osrm.test")
    _serve(routing, ok({"code": "Ok", **_ors_body()}))

    matrix = await _matrix(TransportMode.DRIVING)

    [request] = routing
    assert request.method == "GET"
    assert request.url.path.startswith("/table/v1/driving/2.170100,41.387000;")
    assert request.url.params["annotations"] == "distance,duration"
    assert matrix.estimated is False
    assert matrix.provider == "osrm"


async def test_a_repeat_request_is_served_from_cache(routing: list[httpx.Request]) -> None:
    _serve(routing, ok(_ors_body()))
    first = await _matrix()
    second = await _matrix()
    assert len(routing) == 1
    assert second is first


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


async def test_a_transient_error_is_retried(routing: list[httpx.Request]) -> None:
    _serve(routing, status(503), ok(_ors_body()))
    matrix = await _matrix()
    assert len(routing) == 2
    assert matrix.estimated is False


async def test_persistent_rate_limiting_falls_back_honestly(
    routing: list[httpx.Request],
) -> None:
    _serve(routing, status(429))
    matrix = await _matrix()
    assert len(routing) == 3  # one try and two retries
    assert matrix.estimated is True
    assert matrix.fallback_reason == "http_429"


async def test_a_bad_key_is_not_retried(routing: list[httpx.Request]) -> None:
    _serve(routing, status(401))
    matrix = await _matrix()
    assert len(routing) == 1
    assert matrix.fallback_reason == "http_401"


async def test_a_timeout_falls_back(routing: list[httpx.Request]) -> None:
    def hang(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    _serve(routing, hang)
    matrix = await _matrix()
    assert matrix.fallback_reason == "timeout"
    assert len(routing) == 3


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (_ors_body(meters=None), "unroutable_pair"),
        (_ors_body(seconds=-5.0), "malformed_response"),
        ({"distances": [[0.0]], "durations": [[0.0]]}, "malformed_response"),
        ({"unexpected": True}, "malformed_response"),
    ],
)
async def test_an_unusable_reply_is_rejected_whole(
    routing: list[httpx.Request], body: dict, reason: str
) -> None:
    _serve(routing, ok(body))
    matrix = await _matrix()
    assert matrix.estimated is True
    assert matrix.fallback_reason == reason


async def test_the_breaker_stops_calling_a_failing_provider(
    monkeypatch: pytest.MonkeyPatch, routing: list[httpx.Request]
) -> None:
    monkeypatch.setattr(settings, "routing_max_retries", 0)
    _serve(routing, status(500))

    for _ in range(3):
        await _matrix()
    assert len(routing) == 3

    matrix = await _matrix()
    assert matrix.fallback_reason == "circuit_open"
    assert len(routing) == 3  # no fourth call


# ---------------------------------------------------------------------------
# Parity: every engine plans and reports on the matrix it is given
# ---------------------------------------------------------------------------


def _request(matrix: TravelMatrix | None) -> OptimizationRequest:
    places = [
        OptimizationPlace(ref=f"p{i}", name=f"Place {i}", coordinate=c, visit_minutes=30)
        for i, c in enumerate([*POINTS[1:], Coordinate(41.3917, 2.1649)])
    ]
    return OptimizationRequest(
        places=places,
        days=[DayConfig(0, date(2026, 5, 4), 0, 9 * 60, 21 * 60)],
        origin=POINTS[0],
        max_places_per_day=5,
        max_daily_distance_km=500.0,
        time_limit_seconds=1,
        matrix=matrix,
    )


def _measured(size: int = 4) -> TravelMatrix:
    """A road network nothing like the crow-flies one: every leg is 7 km, 40 min."""
    distance = np.full((size, size), 7.0)
    minutes = np.full((size, size), 40)
    np.fill_diagonal(distance, 0.0)
    np.fill_diagonal(minutes, 0)
    return TravelMatrix(distance, minutes, estimated=False, provider="mock")


def test_every_algorithm_reports_legs_from_the_supplied_matrix() -> None:
    request = _request(_measured())
    results = compare_algorithms(request)
    assert {r.engine for r in results} >= {a.value for a in algorithms.NATIVE_ALGORITHMS}

    for result in results:
        assert result.feasible, result.engine
        for day in result.days:
            for stop in day.stops:
                # 7 km stays a walk only below the transit threshold; it is
                # above it, so the leg shows as transit and is re-estimated
                # for that mode. The distance is the measured one regardless.
                assert stop.travel_km == pytest.approx(7.0), result.engine
            assert day.total_distance_km == pytest.approx(7.0 * len(day.stops)), (
                result.engine
            )


def test_measured_durations_are_used_when_the_leg_keeps_its_mode() -> None:
    measured = _measured()
    # Short legs, so walking stays walking and the measured 40 min is reported.
    measured.distance_km[measured.distance_km > 0] = 1.0
    result = optimize(_request(measured), OptimizerAlgorithm.CHEAPEST_INSERTION)
    later_legs = result.days[0].stops[1:]
    assert later_legs
    assert all(s.travel_minutes == 40 for s in later_legs)
    assert all(s.mode is TransportMode.WALKING for s in later_legs)


def test_the_shared_evaluator_prices_on_the_supplied_matrix() -> None:
    plan = algorithms.solve(_request(None), OptimizerAlgorithm.CHEAPEST_INSERTION)
    stops = plan.scheduled_count
    assert stops == 3 and not plan.dropped

    # Re-priced on a network where every hop is 40 minutes: one leg out to each
    # stop, one back to base, plus 30 minutes at each stop. Nothing else.
    assert (
        algorithms.objective_of(_request(_measured()), plan)
        == 40 * (stops + 1) + 30 * stops
    )


def test_a_matrix_of_the_wrong_size_is_refused() -> None:
    with pytest.raises(ValueError, match="expected 4"):
        _request(_measured(size=3)).travel_matrix()


def test_without_a_matrix_the_estimate_is_unchanged() -> None:
    """The default path must produce exactly what it did before routing existed."""
    request = _request(None)
    matrix = request.travel_matrix()
    coords = [request.origin] + [p.coordinate for p in request.places]
    distances, durations = build_travel_matrix(coords, request.mode)
    assert matrix.estimated is True
    assert np.array_equal(matrix.distance_km, distances)
    assert np.array_equal(matrix.duration_minutes, durations)
