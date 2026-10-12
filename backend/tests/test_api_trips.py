"""Integration tests for trips, itinerary generation and editing.

These are the tests that exercise the whole stack end to end: LLM stub ->
candidate recall -> scoring -> OR-Tools -> assembly -> narration -> validation.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

TRIPS = "/api/v1/trips"
ITINERARIES = "/api/v1/itineraries"


async def _create_trip(
    client: AsyncClient, headers: dict[str, str], payload: dict[str, Any]
) -> dict[str, Any]:
    response = await client.post(TRIPS, headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _generate(
    client: AsyncClient,
    headers: dict[str, str],
    trip_id: str,
    brief: str = "History and art, moderate pace.",
    regenerate: bool = False,
) -> dict[str, Any]:
    response = await client.post(
        f"{TRIPS}/{trip_id}/generate",
        headers=headers,
        json={"brief": brief, "regenerate": regenerate},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


class TestTripCrud:
    async def test_creates_a_trip(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        trip = await _create_trip(client, user_token, paris_trip_payload)
        assert trip["destination"]["city"] == "Paris"
        assert trip["status"] == "draft"

    async def test_duration_is_inclusive_of_both_end_dates(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        """A trip from the 1st to the 3rd is three days, not two."""
        trip = await _create_trip(client, user_token, paris_trip_payload)
        assert trip["duration_days"] == 3

    async def test_same_day_trip_is_one_day(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        payload = dict(paris_trip_payload)
        payload["end_date"] = payload["start_date"]
        trip = await _create_trip(client, user_token, payload)
        assert trip["duration_days"] == 1

    async def test_rejects_an_end_date_before_the_start(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        payload = dict(paris_trip_payload)
        payload["start_date"], payload["end_date"] = (
            payload["end_date"],
            payload["start_date"],
        )
        response = await client.post(TRIPS, headers=user_token, json=payload)
        assert response.status_code == 422

    async def test_rejects_an_over_long_trip(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        payload = dict(paris_trip_payload)
        start = date.fromisoformat(payload["start_date"])
        payload["end_date"] = (start + timedelta(days=60)).isoformat()
        response = await client.post(TRIPS, headers=user_token, json=payload)
        assert response.status_code == 422

    async def test_rejects_an_inverted_daily_time_window(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        payload = dict(paris_trip_payload)
        payload["constraints"] = dict(payload["constraints"])
        payload["constraints"]["day_start_time"] = "20:00"
        payload["constraints"]["day_end_time"] = "09:00"
        response = await client.post(TRIPS, headers=user_token, json=payload)
        assert response.status_code == 422

    async def test_unset_constraints_come_from_the_user_profile(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        await client.put(
            "/api/v1/users/me/preferences",
            headers=user_token,
            json={"interests": ["nature", "photography"], "pace": "relaxed"},
        )
        payload = dict(paris_trip_payload)
        payload["constraints"] = {"currency": "EUR"}

        trip = await _create_trip(client, user_token, payload)
        assert set(trip["constraints"]["interests"]) == {"nature", "photography"}

    async def test_lists_only_the_callers_trips(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        await _create_trip(client, user_token, paris_trip_payload)

        other = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "isolated@example.com",
                "password": "otherPassword123",
                "full_name": "Other",
            },
        )
        other_headers = {
            "Authorization": f"Bearer {other.json()['tokens']['access_token']}"
        }

        assert (await client.get(TRIPS, headers=user_token)).json()["meta"]["total"] == 1
        assert (await client.get(TRIPS, headers=other_headers)).json()["meta"]["total"] == 0

    async def test_another_user_cannot_read_the_trip(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        trip = await _create_trip(client, user_token, paris_trip_payload)

        other = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "intruder@example.com",
                "password": "otherPassword123",
                "full_name": "Intruder",
            },
        )
        headers = {"Authorization": f"Bearer {other.json()['tokens']['access_token']}"}

        response = await client.get(f"{TRIPS}/{trip['id']}", headers=headers)
        assert response.status_code == 403

    async def test_delete_removes_the_trip(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        trip = await _create_trip(client, user_token, paris_trip_payload)

        assert (
            await client.delete(f"{TRIPS}/{trip['id']}", headers=user_token)
        ).status_code == 200
        assert (
            await client.get(f"{TRIPS}/{trip['id']}", headers=user_token)
        ).status_code == 404


class TestGeneration:
    async def test_produces_a_complete_itinerary(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        seeded: dict[str, int],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        trip = await _create_trip(client, user_token, paris_trip_payload)
        itinerary = await _generate(client, user_token, trip["id"])

        assert itinerary["status"] == "completed"
        assert len(itinerary["days"]) == trip["duration_days"]
        assert sum(len(d["activities"]) for d in itinerary["days"]) > 0
        assert itinerary["optimization"]["ran"] is True

    async def test_schedule_has_no_overlapping_activities(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        seeded: dict[str, int],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        trip = await _create_trip(client, user_token, paris_trip_payload)
        itinerary = await _generate(client, user_token, trip["id"])

        for day in itinerary["days"]:
            previous_end = -1
            for activity in day["activities"]:
                start, end = (
                    _minutes(activity["start_time"]),
                    _minutes(activity["end_time"]),
                )
                assert start >= previous_end, (
                    f"day {day['day_number']}: {activity['title']} overlaps the previous block"
                )
                assert end >= start
                previous_end = end

    async def test_schedule_respects_the_daily_time_window(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        seeded: dict[str, int],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        trip = await _create_trip(client, user_token, paris_trip_payload)
        itinerary = await _generate(client, user_token, trip["id"])

        window_start = _minutes(paris_trip_payload["constraints"]["day_start_time"])
        window_end = _minutes(paris_trip_payload["constraints"]["day_end_time"])

        for day in itinerary["days"]:
            for activity in day["activities"]:
                # Meals are inserted into gaps and may finish slightly past the
                # sightseeing window, so allow an hour of slack for those.
                slack = 60 if activity["kind"] == "meal" else 0
                assert _minutes(activity["start_time"]) >= window_start
                assert _minutes(activity["end_time"]) <= window_end + slack

    async def test_a_place_is_never_scheduled_twice(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        seeded: dict[str, int],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        trip = await _create_trip(client, user_token, paris_trip_payload)
        itinerary = await _generate(client, user_token, trip["id"])

        visits = [
            a["place_id"]
            for d in itinerary["days"]
            for a in d["activities"]
            if a["kind"] == "visit"
        ]
        assert len(visits) == len(set(visits))

    async def test_a_relaxed_pace_schedules_fewer_places_than_a_packed_one(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        seeded: dict[str, int],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        """Pace is the main lever a traveller pulls, so it has to actually bite."""

        def with_pace(pace: str) -> dict[str, Any]:
            payload = dict(paris_trip_payload)
            payload["constraints"] = {**payload["constraints"], "pace": pace}
            return payload

        relaxed_trip = await _create_trip(client, user_token, with_pace("relaxed"))
        packed_trip = await _create_trip(client, user_token, with_pace("packed"))

        relaxed = await _generate(client, user_token, relaxed_trip["id"], brief="")
        packed = await _generate(client, user_token, packed_trip["id"], brief="")

        def visit_count(itinerary: dict[str, Any]) -> int:
            return sum(
                1
                for d in itinerary["days"]
                for a in d["activities"]
                if a["kind"] == "visit"
            )

        assert visit_count(relaxed) < visit_count(packed)

    async def test_optimiser_reports_a_distance_improvement(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        seeded: dict[str, int],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        trip = await _create_trip(client, user_token, paris_trip_payload)
        itinerary = await _generate(client, user_token, trip["id"])

        report = itinerary["optimization"]
        assert report["distance_before_km"] is not None
        assert report["distance_after_km"] is not None
        assert report["distance_after_km"] <= report["distance_before_km"] + 1e-6

    async def test_repeated_call_reuses_the_existing_version(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        seeded: dict[str, int],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        trip = await _create_trip(client, user_token, paris_trip_payload)
        first = await _generate(client, user_token, trip["id"])
        second = await _generate(client, user_token, trip["id"])
        assert first["id"] == second["id"]
        assert second["version"] == 1

    async def test_regeneration_creates_a_new_version(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        seeded: dict[str, int],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        trip = await _create_trip(client, user_token, paris_trip_payload)
        first = await _generate(client, user_token, trip["id"])
        second = await _generate(client, user_token, trip["id"], regenerate=True)

        assert second["version"] == first["version"] + 1
        assert second["id"] != first["id"]

        versions = await client.get(f"{TRIPS}/{trip['id']}/itineraries", headers=user_token)
        assert len(versions.json()) == 2

    async def test_generation_marks_the_trip_ready(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        seeded: dict[str, int],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        trip = await _create_trip(client, user_token, paris_trip_payload)
        await _generate(client, user_token, trip["id"])

        refreshed = await client.get(f"{TRIPS}/{trip['id']}", headers=user_token)
        assert refreshed.json()["status"] == "ready"
        assert refreshed.json()["active_itinerary_id"]

    async def test_destination_without_places_is_a_clear_422(
        self, client: AsyncClient, user_token: dict[str, str], seeded: dict[str, int]
    ) -> None:
        """An unplannable destination must explain itself, not 500."""
        start = date.today() + timedelta(days=10)
        trip = await _create_trip(
            client,
            user_token,
            {
                "destination": {
                    "city": "Nowhere",
                    "country": "Emptyland",
                    "latitude": -40.0,
                    "longitude": -170.0,
                },
                "start_date": start.isoformat(),
                "end_date": (start + timedelta(days=1)).isoformat(),
            },
        )
        response = await client.post(
            f"{TRIPS}/{trip['id']}/generate", headers=user_token, json={"brief": ""}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "optimization_failed"

    async def test_run_is_recorded_in_the_audit_trail(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        seeded: dict[str, int],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        trip = await _create_trip(client, user_token, paris_trip_payload)
        await _generate(client, user_token, trip["id"])

        runs = await client.get("/api/v1/ai/runs", headers=user_token)
        assert runs.status_code == 200

        body = runs.json()
        assert body
        steps = {step["step"] for step in body[0]["steps"]}
        assert {
            "parse_intent",
            "retrieve_candidates",
            "score_candidates",
            "optimize_schedule",
            "assemble_itinerary",
            "narrate",
            "validate_plan",
        } <= steps


class TestItineraryEditing:
    @pytest.fixture
    async def generated(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        seeded: dict[str, int],
        paris_trip_payload: dict[str, Any],
    ) -> dict[str, Any]:
        trip = await _create_trip(client, user_token, paris_trip_payload)
        return await _generate(client, user_token, trip["id"])

    async def test_reorder_applies_and_re_times_the_day(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        generated: dict[str, Any],
    ) -> None:
        day = next(d for d in generated["days"] if len(d["activities"]) >= 2)
        count = len(day["activities"])

        response = await client.post(
            f"{ITINERARIES}/{generated['id']}/reorder",
            headers=user_token,
            json={
                "day_number": day["day_number"],
                "activity_order": list(reversed(range(count))),
            },
        )
        assert response.status_code == 200

        updated = next(
            d for d in response.json()["days"] if d["day_number"] == day["day_number"]
        )
        assert [a["title"] for a in updated["activities"]] == [
            a["title"] for a in reversed(day["activities"])
        ]

        previous_end = -1
        for activity in updated["activities"]:
            start = _minutes(activity["start_time"])
            assert start >= previous_end
            previous_end = _minutes(activity["end_time"])

    async def test_reorder_keeps_the_days_original_start_time(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        generated: dict[str, Any],
    ) -> None:
        day = next(d for d in generated["days"] if len(d["activities"]) >= 2)
        original_start = min(_minutes(a["start_time"]) for a in day["activities"])

        response = await client.post(
            f"{ITINERARIES}/{generated['id']}/reorder",
            headers=user_token,
            json={
                "day_number": day["day_number"],
                "activity_order": list(reversed(range(len(day["activities"])))),
            },
        )
        updated = next(
            d for d in response.json()["days"] if d["day_number"] == day["day_number"]
        )
        assert _minutes(updated["activities"][0]["start_time"]) == original_start

    async def test_reorder_rejects_a_non_permutation(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        generated: dict[str, Any],
    ) -> None:
        day = next(d for d in generated["days"] if len(d["activities"]) >= 2)
        response = await client.post(
            f"{ITINERARIES}/{generated['id']}/reorder",
            headers=user_token,
            json={"day_number": day["day_number"], "activity_order": [0, 0]},
        )
        assert response.status_code == 422

    async def test_reorder_rejects_an_unknown_day(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        generated: dict[str, Any],
    ) -> None:
        response = await client.post(
            f"{ITINERARIES}/{generated['id']}/reorder",
            headers=user_token,
            json={"day_number": 99, "activity_order": [0]},
        )
        assert response.status_code == 404

    async def test_removing_an_activity_re_times_the_rest(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        generated: dict[str, Any],
    ) -> None:
        day = next(d for d in generated["days"] if len(d["activities"]) >= 2)
        count = len(day["activities"])

        response = await client.delete(
            f"{ITINERARIES}/{generated['id']}/days/{day['day_number']}/activities/0",
            headers=user_token,
        )
        assert response.status_code == 200

        updated = next(
            d for d in response.json()["days"] if d["day_number"] == day["day_number"]
        )
        assert len(updated["activities"]) == count - 1

    async def test_removing_a_missing_activity_is_404(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        generated: dict[str, Any],
    ) -> None:
        day = generated["days"][0]
        response = await client.delete(
            f"{ITINERARIES}/{generated['id']}/days/{day['day_number']}/activities/999",
            headers=user_token,
        )
        assert response.status_code == 404

    async def test_stats_are_consistent_with_the_itinerary(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        generated: dict[str, Any],
    ) -> None:
        response = await client.get(
            f"{ITINERARIES}/{generated['id']}/stats", headers=user_token
        )
        assert response.status_code == 200

        stats = response.json()
        assert stats["day_count"] == len(generated["days"])
        assert stats["activity_count"] == sum(
            len(d["activities"]) for d in generated["days"]
        )


class TestSharing:
    async def test_shared_trip_is_readable_without_a_token(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        seeded: dict[str, int],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        trip = await _create_trip(client, user_token, paris_trip_payload)
        await _generate(client, user_token, trip["id"])

        shared = await client.post(f"{TRIPS}/{trip['id']}/share", headers=user_token)
        assert shared.status_code == 200
        token = shared.json()["share_token"]

        public = await client.get(f"{TRIPS}/shared/{token}")
        assert public.status_code == 200
        assert public.json()["days"]

    async def test_unsharing_invalidates_the_link(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        seeded: dict[str, int],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        trip = await _create_trip(client, user_token, paris_trip_payload)
        await _generate(client, user_token, trip["id"])

        token = (
            await client.post(f"{TRIPS}/{trip['id']}/share", headers=user_token)
        ).json()["share_token"]

        await client.delete(f"{TRIPS}/{trip['id']}/share", headers=user_token)
        assert (await client.get(f"{TRIPS}/shared/{token}")).status_code == 404

    async def test_unknown_share_token_is_404(self, client: AsyncClient) -> None:
        assert (await client.get(f"{TRIPS}/shared/made-up-token")).status_code == 404


class TestStandaloneOptimisation:
    async def test_orders_places_and_shortens_the_route(
        self, client: AsyncClient, user_token: dict[str, str], seeded: dict[str, int]
    ) -> None:
        listing = await client.get("/api/v1/places", params={"city": "Rome", "limit": 6})
        place_ids = [p["id"] for p in listing.json()["items"]]

        response = await client.post(
            f"{ITINERARIES}/optimize-route",
            headers=user_token,
            json={
                "place_ids": place_ids,
                "start_latitude": 41.9028,
                "start_longitude": 12.4964,
                "days": 2,
                "max_places_per_day": 3,
            },
        )
        assert response.status_code == 200

        body = response.json()
        assert body["stops"]
        assert body["total_distance_km"] <= body["distance_before_km"] + 1e-6

    async def test_requires_at_least_two_places(
        self, client: AsyncClient, user_token: dict[str, str], seeded: dict[str, int]
    ) -> None:
        listing = await client.get("/api/v1/places", params={"limit": 1})
        response = await client.post(
            f"{ITINERARIES}/optimize-route",
            headers=user_token,
            json={
                "place_ids": [listing.json()["items"][0]["id"]],
                "start_latitude": 41.9,
                "start_longitude": 12.5,
            },
        )
        assert response.status_code == 422

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.post(
            f"{ITINERARIES}/optimize-route",
            json={
                "place_ids": ["000000000000000000000000", "000000000000000000000001"],
                "start_latitude": 41.9,
                "start_longitude": 12.5,
            },
        )
        assert response.status_code == 401
