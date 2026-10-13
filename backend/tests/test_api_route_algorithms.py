"""API-level tests for the route-comparison surface.

``tests/test_route_algorithms.py`` covers the algorithms themselves against the
solver. These cover the **HTTP boundary** on a real MongoDB: authentication,
payload validation, and the invariants the frontend relies on — exactly one run
marked best, gaps measured against it, and runs returned for every algorithm.

They skip automatically when no MongoDB is reachable (see ``conftest.py``).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

ITINERARIES = "/api/v1/itineraries"
PLACES = "/api/v1/places"

# Rome's centre, used as the starting point for every route below.
ROME = {"start_latitude": 41.9028, "start_longitude": 12.4964}


async def rome_place_ids(client: AsyncClient, limit: int = 6) -> list[str]:
    response = await client.get(PLACES, params={"city": "Rome", "limit": limit})
    assert response.status_code == 200, response.text
    return [place["id"] for place in response.json()["items"]]


def compare_payload(place_ids: list[str], **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "place_ids": place_ids,
        **ROME,
        "days": 1,
        "max_places_per_day": 6,
        "day_start_time": "09:00",
        "day_end_time": "19:00",
        # Pinned, because opening hours are checked against this date's weekday.
        "start_date": (date.today() + timedelta(days=30)).isoformat(),
        "time_limit_seconds": 1,
    }
    payload.update(overrides)
    return payload


class TestAlgorithmCatalogue:
    async def test_lists_every_algorithm(
        self, client: AsyncClient, seeded: dict[str, int], user_token: dict[str, str]
    ) -> None:
        response = await client.get(
            f"{ITINERARIES}/optimizer/algorithms", headers=user_token
        )
        assert response.status_code == 200

        algorithms = response.json()
        keys = {entry["key"] for entry in algorithms}
        assert {
            "auto",
            "ortools",
            "nearest_neighbor",
            "cheapest_insertion",
            "local_search",
            "simulated_annealing",
            "genetic",
        } <= keys

    async def test_each_entry_explains_its_tradeoff(
        self, client: AsyncClient, seeded: dict[str, int], user_token: dict[str, str]
    ) -> None:
        """The UI shows these verbatim, so an empty field would render blank."""
        response = await client.get(
            f"{ITINERARIES}/optimizer/algorithms", headers=user_token
        )
        for entry in response.json():
            for field in ("key", "label", "speed", "quality", "description"):
                assert entry.get(field), f"{entry.get('key')} has no {field}"

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.get(f"{ITINERARIES}/optimizer/algorithms")
        assert response.status_code == 401


class TestCompareAuthentication:
    async def test_requires_authentication(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        ids = await rome_place_ids(client, 3)
        response = await client.post(
            f"{ITINERARIES}/optimize-route/compare", json=compare_payload(ids)
        )
        assert response.status_code == 401

    async def test_rejects_a_malformed_token(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        ids = await rome_place_ids(client, 3)
        response = await client.post(
            f"{ITINERARIES}/optimize-route/compare",
            headers={"Authorization": "Bearer not-a-real-token"},
            json=compare_payload(ids),
        )
        assert response.status_code == 401


class TestCompareValidation:
    async def test_rejects_fewer_than_two_places(
        self, client: AsyncClient, seeded: dict[str, int], user_token: dict[str, str]
    ) -> None:
        """One place is not a route; the schema enforces a minimum of two."""
        ids = await rome_place_ids(client, 1)
        response = await client.post(
            f"{ITINERARIES}/optimize-route/compare",
            headers=user_token,
            json=compare_payload(ids),
        )
        assert response.status_code == 422

    async def test_rejects_an_unknown_algorithm(
        self, client: AsyncClient, seeded: dict[str, int], user_token: dict[str, str]
    ) -> None:
        ids = await rome_place_ids(client, 3)
        response = await client.post(
            f"{ITINERARIES}/optimize-route/compare",
            headers=user_token,
            json=compare_payload(ids, algorithms=["teleportation"]),
        )
        assert response.status_code == 422

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("start_latitude", 91.0),
            ("start_longitude", 181.0),
            ("max_places_per_day", 99),
            ("day_start_time", "25:00"),
            ("time_limit_seconds", 999),
        ],
    )
    async def test_rejects_out_of_range_values(
        self,
        client: AsyncClient,
        seeded: dict[str, int],
        user_token: dict[str, str],
        field: str,
        value: Any,
    ) -> None:
        ids = await rome_place_ids(client, 3)
        response = await client.post(
            f"{ITINERARIES}/optimize-route/compare",
            headers=user_token,
            json=compare_payload(ids, **{field: value}),
        )
        assert response.status_code == 422

    async def test_unknown_place_ids_are_not_found(
        self, client: AsyncClient, seeded: dict[str, int], user_token: dict[str, str]
    ) -> None:
        """Well-formed ids that resolve to nothing are a 404, not a 500."""
        response = await client.post(
            f"{ITINERARIES}/optimize-route/compare",
            headers=user_token,
            json=compare_payload(["0" * 24, "1" * 24, "2" * 24]),
        )
        assert response.status_code == 404

    async def test_a_single_resolvable_id_is_not_found(
        self, client: AsyncClient, seeded: dict[str, int], user_token: dict[str, str]
    ) -> None:
        """Two ids supplied, one real: still fewer than two places to route."""
        ids = await rome_place_ids(client, 1)
        response = await client.post(
            f"{ITINERARIES}/optimize-route/compare",
            headers=user_token,
            json=compare_payload([*ids, "0" * 24]),
        )
        assert response.status_code == 404


class TestCompareResults:
    @pytest.fixture
    async def comparison(
        self, client: AsyncClient, seeded: dict[str, int], user_token: dict[str, str]
    ) -> dict[str, Any]:
        ids = await rome_place_ids(client, 6)
        response = await client.post(
            f"{ITINERARIES}/optimize-route/compare",
            headers=user_token,
            json=compare_payload(ids),
        )
        assert response.status_code == 200, response.text
        return response.json()

    async def test_returns_a_run_for_every_algorithm(
        self, comparison: dict[str, Any]
    ) -> None:
        assert len(comparison["runs"]) == 6

    async def test_exactly_one_run_is_best(self, comparison: dict[str, Any]) -> None:
        """The UI's "Best" badge assumes this; two would render twice."""
        assert sum(1 for run in comparison["runs"] if run["is_best"]) == 1

    async def test_best_algorithm_names_the_best_run(
        self, comparison: dict[str, Any]
    ) -> None:
        best = next(run for run in comparison["runs"] if run["is_best"])
        assert comparison["best_algorithm"] == best["algorithm"]

    async def test_best_run_has_the_lowest_cost(self, comparison: dict[str, Any]) -> None:
        """The whole comparison is meaningless if "best" is not actually best."""
        best = next(run for run in comparison["runs"] if run["is_best"])
        feasible = [r for r in comparison["runs"] if r["scheduled_count"] > 0]
        assert all(run["objective"] >= best["objective"] for run in feasible)

    async def test_gaps_are_measured_against_the_best(
        self, comparison: dict[str, Any]
    ) -> None:
        best = next(run for run in comparison["runs"] if run["is_best"])
        assert best["gap_percent"] == pytest.approx(0.0, abs=0.01)

        for run in comparison["runs"]:
            if run["scheduled_count"] == 0 or run["is_best"]:
                continue
            expected = (run["objective"] - best["objective"]) / best["objective"] * 100
            assert run["gap_percent"] == pytest.approx(expected, abs=0.02)

    async def test_fastest_algorithm_is_the_quickest_run(
        self, comparison: dict[str, Any]
    ) -> None:
        quickest = min(comparison["runs"], key=lambda run: run["duration_seconds"])
        assert comparison["fastest_algorithm"] == quickest["algorithm"]

    async def test_stops_are_ordered_and_timed(self, comparison: dict[str, Any]) -> None:
        for run in comparison["runs"]:
            if not run["stops"]:
                continue
            orders = [stop["order"] for stop in run["stops"]]
            assert orders == sorted(orders)
            for stop in run["stops"]:
                assert stop["arrival_time"] <= stop["departure_time"]

    async def test_no_place_is_visited_twice(self, comparison: dict[str, Any]) -> None:
        for run in comparison["runs"]:
            visited = [stop["place_id"] for stop in run["stops"]]
            assert len(visited) == len(set(visited)), run["algorithm"]

    async def test_scheduled_and_dropped_account_for_every_place(
        self, comparison: dict[str, Any]
    ) -> None:
        for run in comparison["runs"]:
            assert run["scheduled_count"] + run["dropped_count"] == 6, run["algorithm"]

    async def test_a_subset_of_algorithms_can_be_requested(
        self, client: AsyncClient, seeded: dict[str, int], user_token: dict[str, str]
    ) -> None:
        ids = await rome_place_ids(client, 4)
        response = await client.post(
            f"{ITINERARIES}/optimize-route/compare",
            headers=user_token,
            json=compare_payload(ids, algorithms=["nearest_neighbor", "local_search"]),
        )
        assert response.status_code == 200

        returned = {run["algorithm"] for run in response.json()["runs"]}
        assert returned == {"nearest_neighbor", "local_search"}

    async def test_the_same_request_gives_the_same_plans(
        self, client: AsyncClient, seeded: dict[str, int], user_token: dict[str, str]
    ) -> None:
        """Randomised algorithms are seeded, so results must be reproducible."""
        ids = await rome_place_ids(client, 5)
        payload = compare_payload(ids)

        first = await client.post(
            f"{ITINERARIES}/optimize-route/compare", headers=user_token, json=payload
        )
        second = await client.post(
            f"{ITINERARIES}/optimize-route/compare", headers=user_token, json=payload
        )
        assert first.status_code == second.status_code == 200

        def plans(body: dict[str, Any]) -> dict[str, list[str]]:
            return {
                run["algorithm"]: [stop["place_id"] for stop in run["stops"]]
                for run in body["runs"]
            }

        assert plans(first.json()) == plans(second.json())


class TestOptimizeRouteAlgorithmParameter:
    async def test_accepts_a_named_algorithm(
        self, client: AsyncClient, seeded: dict[str, int], user_token: dict[str, str]
    ) -> None:
        ids = await rome_place_ids(client, 5)
        response = await client.post(
            f"{ITINERARIES}/optimize-route",
            headers=user_token,
            json=compare_payload(ids, algorithm="local_search", time_limit_seconds=2),
        )
        assert response.status_code == 200
        assert response.json()["engine"] == "local_search"

    async def test_rejects_an_unknown_algorithm(
        self, client: AsyncClient, seeded: dict[str, int], user_token: dict[str, str]
    ) -> None:
        ids = await rome_place_ids(client, 3)
        response = await client.post(
            f"{ITINERARIES}/optimize-route",
            headers=user_token,
            json=compare_payload(ids, algorithm="warp-drive", time_limit_seconds=2),
        )
        assert response.status_code == 422

    async def test_reports_the_plan_cost(
        self, client: AsyncClient, seeded: dict[str, int], user_token: dict[str, str]
    ) -> None:
        """`objective` is what makes two plans comparable, so it must be present."""
        ids = await rome_place_ids(client, 5)
        response = await client.post(
            f"{ITINERARIES}/optimize-route",
            headers=user_token,
            json=compare_payload(ids, algorithm="ortools", time_limit_seconds=2),
        )
        assert response.status_code == 200
        assert isinstance(response.json()["objective"], int)
