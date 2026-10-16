"""Road routing at the HTTP boundary.

What a client can rely on: every route response says whether its figures were
measured or estimated; a measured matrix reaches every algorithm's figures and
the legs of an applied route; and a failing provider degrades to the estimate
instead of failing the request.

The provider is an ``httpx.MockTransport``, so nothing leaves the machine.
These need MongoDB and skip without it (see ``conftest.py``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from app.core.config import RoutingProviderName, settings
from app.services import routing_service
from tests.test_api_apply_route import _planned_trip, _routable
from tests.test_api_route_algorithms import ITINERARIES, compare_payload, rome_place_ids

pytestmark = pytest.mark.asyncio

# Every measured leg in these tests: 3 km, 15 minutes of walking.
LEG_METERS = 3000.0
LEG_SECONDS = 900.0


def _measured_reply(request: httpx.Request) -> httpx.Response:
    size = len(json.loads(request.content)["locations"])
    square = [[0.0 if i == j else 1.0 for j in range(size)] for i in range(size)]
    return httpx.Response(
        200,
        json={
            "distances": [[v * LEG_METERS for v in row] for row in square],
            "durations": [[v * LEG_SECONDS for v in row] for row in square],
        },
    )


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    """openrouteservice configured and answered locally; yields the requests seen."""
    monkeypatch.setattr(settings, "routing_provider", RoutingProviderName.OPENROUTESERVICE)
    monkeypatch.setattr(settings, "routing_base_url", "https://ors.test")
    monkeypatch.setattr(settings, "routing_retry_backoff_seconds", 0.0)
    routing_service.reset_state()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _measured_reply(request)

    routing_service.transport = httpx.MockTransport(handler)
    yield seen
    routing_service.transport = None
    routing_service.reset_state()


async def _compare(client: AsyncClient, token: dict[str, str]) -> dict[str, Any]:
    ids = await rome_place_ids(client, limit=5)
    response = await client.post(
        f"{ITINERARIES}/optimize-route/compare",
        headers=token,
        json=compare_payload(ids, algorithms=["nearest_neighbor", "cheapest_insertion"]),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_estimates_are_labelled_by_default(
    client: AsyncClient, seeded: dict[str, int], user_token: dict[str, str]
) -> None:
    body = await _compare(client, user_token)
    assert body["estimated"] is True
    assert body["routing_provider"] == "haversine"


async def test_a_measured_matrix_reaches_every_run(
    client: AsyncClient,
    seeded: dict[str, int],
    user_token: dict[str, str],
    provider: list[httpx.Request],
) -> None:
    body = await _compare(client, user_token)

    assert len(provider) == 1  # one matrix for every algorithm, not one each
    assert body["estimated"] is False
    assert body["routing_provider"] == "openrouteservice"
    for run in body["runs"]:
        assert run["stops"], run["algorithm"]
        for stop in run["stops"]:
            assert stop["travel_km"] == pytest.approx(LEG_METERS / 1000), run["algorithm"]


async def test_a_failing_provider_falls_back_instead_of_failing(
    client: AsyncClient,
    seeded: dict[str, int],
    user_token: dict[str, str],
    provider: list[httpx.Request],
) -> None:
    routing_service.transport = httpx.MockTransport(lambda _r: httpx.Response(503))

    body = await _compare(client, user_token)

    assert body["estimated"] is True
    assert body["routing_provider"] == "haversine"
    assert body["runs"]


async def test_generated_itineraries_record_how_travel_was_priced(
    client: AsyncClient, seeded: dict[str, int], user_token: dict[str, str]
) -> None:
    itinerary = await _planned_trip(client, user_token)

    assert itinerary["optimization"]["estimated"] is True
    assert itinerary["optimization"]["routing_provider"] == "haversine"
    legs = [
        a["travel_from_previous"]
        for day in itinerary["days"]
        for a in day["activities"]
        if a["travel_from_previous"]
    ]
    assert legs
    assert all(leg["estimated"] is True for leg in legs)


async def test_an_applied_route_is_priced_on_the_same_network(
    client: AsyncClient,
    seeded: dict[str, int],
    user_token: dict[str, str],
    provider: list[httpx.Request],
) -> None:
    itinerary = await _planned_trip(client, user_token)
    day = next(d for d in itinerary["days"] if len(_routable(d)) >= 2)
    place_ids = [a["place_id"] for a in _routable(day)][::-1]

    response = await client.post(
        f"{ITINERARIES}/{itinerary['id']}/days/{day['day_number']}/apply-route",
        headers=user_token,
        json={
            "place_ids": place_ids,
            "algorithm": "nearest_neighbor",
            "expected_updated_at": itinerary["updated_at"],
        },
    )
    assert response.status_code == 200, response.text

    updated = next(
        d for d in response.json()["days"] if d["day_number"] == day["day_number"]
    )
    legs = [a["travel_from_previous"] for a in _routable(updated)[1:]]
    assert legs and all(leg is not None for leg in legs)
    for leg in legs:
        assert leg["estimated"] is False
        assert leg["distance_km"] == pytest.approx(LEG_METERS / 1000)
