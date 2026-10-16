"""API-level tests for applying a compared route to a day.

This endpoint rewrites a stored plan from client input, so most of these tests
are about what it *refuses*: a payload from a stale comparison, one that drops or
duplicates a place, one naming a place from somewhere else, and one aimed at
another user's itinerary. The happy path is checked for consistency rather than
just a 200 - times, distances and totals all have to agree afterwards.

They skip automatically when no MongoDB is reachable (see ``conftest.py``).
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

ITINERARIES = "/api/v1/itineraries"
TRIPS = "/api/v1/trips"


async def _planned_trip(
    client: AsyncClient, token: dict[str, str], city: str = "Rome"
) -> dict[str, Any]:
    """Create a trip and generate its itinerary, returning the itinerary body."""
    start = date.today() + timedelta(days=30)
    created = await client.post(
        TRIPS,
        headers=token,
        json={
            "title": f"Apply-route trip to {city}",
            "destination": {
                "city": city,
                "country": "Italy",
                "latitude": 41.9028,
                "longitude": 12.4964,
                "timezone": "Europe/Rome",
            },
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=1)).isoformat(),
            "party": {"adults": 2, "children": 0, "infants": 0},
            "constraints": {
                "pace": "moderate",
                "budget_level": "moderate",
                "currency": "EUR",
                "interests": ["history", "art"],
                "preferred_transport": "walking",
                "day_start_time": "09:00",
                "day_end_time": "20:00",
                "max_daily_walking_km": 10.0,
            },
        },
    )
    assert created.status_code == 201, created.text
    trip_id = created.json()["id"]

    generated = await client.post(f"{TRIPS}/{trip_id}/generate", headers=token, json={})
    assert generated.status_code in (200, 201), generated.text
    return generated.json()


def _routable(day: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for a in day["activities"] if a.get("place_id")]


def _payload(
    itinerary: dict[str, Any], day: dict[str, Any], **overrides: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        # Reversed, so a successful apply is visible as a change in order.
        "place_ids": [a["place_id"] for a in reversed(_routable(day))],
        "algorithm": "local_search",
        "expected_updated_at": itinerary["updated_at"],
    }
    body.update(overrides)
    return body


@pytest.fixture
async def planned(
    client: AsyncClient, seeded: dict[str, int], user_token: dict[str, str]
) -> dict[str, Any]:
    return await _planned_trip(client, user_token)


class TestAuth:
    async def test_requires_authentication(
        self, client: AsyncClient, planned: dict[str, Any]
    ) -> None:
        day = planned["days"][0]
        response = await client.post(
            f"{ITINERARIES}/{planned['id']}/days/{day['day_number']}/apply-route",
            json=_payload(planned, day),
        )
        assert response.status_code == 401

    async def test_another_user_cannot_apply_to_someone_elses_itinerary(
        self, client: AsyncClient, planned: dict[str, Any]
    ) -> None:
        """Ownership is enforced server-side, not by the client not asking."""
        import uuid

        other = await client.post(
            "/api/v1/auth/register",
            json={
                "email": f"intruder-{uuid.uuid4().hex[:8]}@example.com",
                "password": "intruderPass123",
                "full_name": "Intruder",
            },
        )
        assert other.status_code == 201, other.text
        headers = {"Authorization": f"Bearer {other.json()['tokens']['access_token']}"}

        day = planned["days"][0]
        response = await client.post(
            f"{ITINERARIES}/{planned['id']}/days/{day['day_number']}/apply-route",
            headers=headers,
            json=_payload(planned, day),
        )
        assert response.status_code in (403, 404), response.text


class TestValidation:
    async def test_unknown_day_is_404(
        self, client: AsyncClient, user_token: dict[str, str], planned: dict[str, Any]
    ) -> None:
        day = planned["days"][0]
        response = await client.post(
            f"{ITINERARIES}/{planned['id']}/days/99/apply-route",
            headers=user_token,
            json=_payload(planned, day),
        )
        assert response.status_code == 404, response.text

    async def test_a_repeated_place_is_rejected(
        self, client: AsyncClient, user_token: dict[str, str], planned: dict[str, Any]
    ) -> None:
        day = planned["days"][0]
        ids = [a["place_id"] for a in _routable(day)]
        response = await client.post(
            f"{ITINERARIES}/{planned['id']}/days/{day['day_number']}/apply-route",
            headers=user_token,
            json=_payload(planned, day, place_ids=[ids[0], *ids]),
        )
        assert response.status_code == 422, response.text

    async def test_a_missing_place_is_rejected(
        self, client: AsyncClient, user_token: dict[str, str], planned: dict[str, Any]
    ) -> None:
        """Applying a route reorders a day; it must not quietly delete from it."""
        day = planned["days"][0]
        ids = [a["place_id"] for a in _routable(day)]
        response = await client.post(
            f"{ITINERARIES}/{planned['id']}/days/{day['day_number']}/apply-route",
            headers=user_token,
            json=_payload(planned, day, place_ids=ids[:-1]),
        )
        assert response.status_code == 422, response.text
        assert "missing_place_ids" in response.json()["error"]["details"]

    async def test_a_place_from_another_day_is_rejected(
        self, client: AsyncClient, user_token: dict[str, str], planned: dict[str, Any]
    ) -> None:
        if len(planned["days"]) < 2:
            pytest.skip("needs a two-day itinerary")
        first, second = planned["days"][0], planned["days"][1]
        foreign = _routable(second)[0]["place_id"]
        ids = [a["place_id"] for a in _routable(first)]

        response = await client.post(
            f"{ITINERARIES}/{planned['id']}/days/{first['day_number']}/apply-route",
            headers=user_token,
            json=_payload(planned, first, place_ids=[*ids[:-1], foreign]),
        )
        assert response.status_code == 422, response.text

    async def test_an_unknown_algorithm_is_422_not_500(
        self, client: AsyncClient, user_token: dict[str, str], planned: dict[str, Any]
    ) -> None:
        day = planned["days"][0]
        response = await client.post(
            f"{ITINERARIES}/{planned['id']}/days/{day['day_number']}/apply-route",
            headers=user_token,
            json=_payload(planned, day, algorithm="by-eye"),
        )
        assert response.status_code == 422, response.text


class TestConcurrency:
    async def test_a_stale_comparison_is_refused_with_409(
        self, client: AsyncClient, user_token: dict[str, str], planned: dict[str, Any]
    ) -> None:
        """The case this check exists for: someone else edited the day meanwhile.

        An `updated_at` from before that edit describes a day that no longer
        exists, and applying it would discard the edit without telling anyone.
        """
        day = planned["days"][0]
        response = await client.post(
            f"{ITINERARIES}/{planned['id']}/days/{day['day_number']}/apply-route",
            headers=user_token,
            json=_payload(planned, day, expected_updated_at="2020-01-01T00:00:00+00:00"),
        )
        assert response.status_code == 409, response.text
        assert "re-run the comparison" in response.json()["error"]["message"].lower()

    async def test_applying_twice_with_the_same_token_is_refused(
        self, client: AsyncClient, user_token: dict[str, str], planned: dict[str, Any]
    ) -> None:
        """The first apply moves `updated_at`, which invalidates the token."""
        day = planned["days"][0]
        url = f"{ITINERARIES}/{planned['id']}/days/{day['day_number']}/apply-route"

        first = await client.post(url, headers=user_token, json=_payload(planned, day))
        assert first.status_code == 200, first.text

        replay = await client.post(url, headers=user_token, json=_payload(planned, day))
        assert replay.status_code == 409, replay.text


class TestApplying:
    async def test_the_order_changes_and_the_day_stays_consistent(
        self, client: AsyncClient, user_token: dict[str, str], planned: dict[str, Any]
    ) -> None:
        day = planned["days"][0]
        before = [a["place_id"] for a in _routable(day)]
        requested = list(reversed(before))

        response = await client.post(
            f"{ITINERARIES}/{planned['id']}/days/{day['day_number']}/apply-route",
            headers=user_token,
            json=_payload(planned, day, place_ids=requested),
        )
        assert response.status_code == 200, response.text

        updated_day = next(
            d for d in response.json()["days"] if d["day_number"] == day["day_number"]
        )
        after = [a["place_id"] for a in _routable(updated_day)]
        assert after == requested, "the requested order should be the stored order"

        # Times must flow forward and the recorded order must match the sequence.
        activities = updated_day["activities"]
        for index, activity in enumerate(activities):
            assert activity["order"] == index
            assert activity["start_time"] < activity["end_time"]
        for earlier, later in pairwise(activities):
            assert earlier["end_time"] <= later["start_time"]

    async def test_the_applied_algorithm_is_recorded(
        self, client: AsyncClient, user_token: dict[str, str], planned: dict[str, Any]
    ) -> None:
        """So the plan says where its order came from, rather than claiming to be solved."""
        day = planned["days"][0]
        response = await client.post(
            f"{ITINERARIES}/{planned['id']}/days/{day['day_number']}/apply-route",
            headers=user_token,
            json=_payload(planned, day, algorithm="genetic"),
        )
        assert response.status_code == 200, response.text
        assert response.json()["optimization"]["solver_status"] == "applied:genetic"

    async def test_totals_are_recomputed_not_taken_from_the_client(
        self, client: AsyncClient, user_token: dict[str, str], planned: dict[str, Any]
    ) -> None:
        """The payload carries no figures at all, so these can only be server-side."""
        day = planned["days"][0]
        response = await client.post(
            f"{ITINERARIES}/{planned['id']}/days/{day['day_number']}/apply-route",
            headers=user_token,
            json=_payload(planned, day),
        )
        assert response.status_code == 200, response.text

        body = response.json()
        updated_day = next(d for d in body["days"] if d["day_number"] == day["day_number"])
        assert updated_day["total_distance_km"] >= 0
        # The first stop has nothing to travel from.
        assert updated_day["activities"][0]["travel_from_previous"] is None
        legs = [
            a["travel_from_previous"]
            for a in updated_day["activities"][1:]
            if a["travel_from_previous"] is not None
        ]
        assert legs, "later stops should carry travel legs"
        assert all(leg["distance_km"] >= 0 for leg in legs)

    async def test_non_routable_activities_keep_their_place_in_the_day(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        planned: dict[str, Any],
        database: Any,
    ) -> None:
        """A block of free time must not be shuffled because a route was applied.

        The generator gives every activity a place, so the condition is made
        here: one middle activity has its `place_id` cleared, which is what a
        free-time block looks like. It should still be at that index afterwards,
        while the places around it are reordered.
        """
        from beanie import PydanticObjectId

        day_number = planned["days"][0]["day_number"]
        pinned_index = 2

        result = await database["itineraries"].update_one(
            {"_id": PydanticObjectId(planned["id"])},
            {
                "$set": {
                    f"days.0.activities.{pinned_index}.place_id": None,
                    f"days.0.activities.{pinned_index}.title": "Free time",
                }
            },
        )
        assert result.modified_count == 1

        # Re-read: that write moved the staleness token.
        refreshed = await client.get(f"{ITINERARIES}/{planned['id']}", headers=user_token)
        assert refreshed.status_code == 200, refreshed.text
        itinerary = refreshed.json()
        day = next(d for d in itinerary["days"] if d["day_number"] == day_number)
        assert day["activities"][pinned_index]["place_id"] is None

        before = [a["place_id"] for a in _routable(day)]
        response = await client.post(
            f"{ITINERARIES}/{planned['id']}/days/{day_number}/apply-route",
            headers=user_token,
            json=_payload(itinerary, day, place_ids=list(reversed(before))),
        )
        assert response.status_code == 200, response.text

        updated_day = next(
            d for d in response.json()["days"] if d["day_number"] == day_number
        )
        assert updated_day["activities"][pinned_index]["place_id"] is None, (
            "free time should still sit at its original index"
        )
        assert [a["place_id"] for a in _routable(updated_day)] == list(reversed(before))
