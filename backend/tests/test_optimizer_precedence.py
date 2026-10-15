"""Which algorithm runs, and what happens when the answer is nonsense.

The precedence order is request > plan > OPTIMIZER_ALGORITHM > auto, decided in
one place (`engine.resolve_algorithm`). These tests pin each rung, because a
silent change here would route every plan through a different solver while every
other test still passed.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import OptimizerAlgorithm, settings
from app.optimization.distance import Coordinate
from app.optimization.engine import resolve_algorithm
from app.optimization.schemas import DayConfig, OptimizationRequest

PARIS = Coordinate(48.8566, 2.3522)
TRIPS = "/api/v1/trips"


def _request(algorithm: str | None = None) -> OptimizationRequest:
    today = date.today()
    return OptimizationRequest(
        places=[],
        days=[
            DayConfig(
                index=0,
                day_date=today,
                weekday=today.weekday(),
                start_minutes=540,
                end_minutes=1200,
            )
        ],
        origin=PARIS,
        algorithm=algorithm,
    )


class TestPrecedence:
    def test_explicit_argument_beats_everything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "optimizer_algorithm", OptimizerAlgorithm.ORTOOLS)
        resolved = resolve_algorithm(_request("local_search"), OptimizerAlgorithm.GENETIC)
        assert resolved is OptimizerAlgorithm.GENETIC

    def test_request_beats_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "optimizer_algorithm", OptimizerAlgorithm.ORTOOLS)
        assert (
            resolve_algorithm(_request("simulated_annealing"))
            is OptimizerAlgorithm.SIMULATED_ANNEALING
        )

    def test_environment_is_used_when_the_request_says_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            settings, "optimizer_algorithm", OptimizerAlgorithm.CHEAPEST_INSERTION
        )
        assert resolve_algorithm(_request(None)) is OptimizerAlgorithm.CHEAPEST_INSERTION

    def test_auto_is_the_last_resort(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty setting must not mean "no algorithm"."""
        monkeypatch.setattr(settings, "optimizer_algorithm", None)
        assert resolve_algorithm(_request(None)) is OptimizerAlgorithm.AUTO

    def test_an_unknown_name_is_rejected_rather_than_guessed(self) -> None:
        with pytest.raises(ValueError):
            resolve_algorithm(_request("travelling-salesman-by-vibes"))

    def test_every_enum_member_resolves_to_itself(self) -> None:
        """Guards against an enum value the resolver cannot actually return."""
        for algorithm in OptimizerAlgorithm:
            assert resolve_algorithm(_request(algorithm.value)) is algorithm


@pytest.mark.asyncio
class TestTripAlgorithmField:
    async def test_defaults_to_unset_so_the_environment_decides(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        response = await client.post(TRIPS, headers=user_token, json=paris_trip_payload)
        assert response.status_code == 201, response.text
        assert response.json()["constraints"]["optimizer_algorithm"] is None

    async def test_round_trips_a_chosen_algorithm(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        paris_trip_payload["constraints"]["optimizer_algorithm"] = "genetic"
        created = await client.post(TRIPS, headers=user_token, json=paris_trip_payload)
        assert created.status_code == 201, created.text
        trip_id = created.json()["id"]

        fetched = await client.get(f"{TRIPS}/{trip_id}", headers=user_token)
        assert fetched.json()["constraints"]["optimizer_algorithm"] == "genetic"

    async def test_can_be_changed_after_creation(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        created = await client.post(TRIPS, headers=user_token, json=paris_trip_payload)
        trip_id = created.json()["id"]
        constraints = created.json()["constraints"]
        constraints["optimizer_algorithm"] = "auto"

        updated = await client.patch(
            f"{TRIPS}/{trip_id}", headers=user_token, json={"constraints": constraints}
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["constraints"]["optimizer_algorithm"] == "auto"

    async def test_an_unknown_algorithm_is_a_422_not_a_500(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        paris_trip_payload: dict[str, Any],
    ) -> None:
        paris_trip_payload["constraints"]["optimizer_algorithm"] = "dijkstra-ish"
        response = await client.post(TRIPS, headers=user_token, json=paris_trip_payload)
        assert response.status_code == 422, response.text

    async def test_a_trip_stored_before_the_field_existed_still_loads(
        self,
        client: AsyncClient,
        user_token: dict[str, str],
        paris_trip_payload: dict[str, Any],
        database: Any,
    ) -> None:
        """The reason this field is optional: no document migration.

        Strips the key from a stored trip to reproduce every document written
        before the field existed, then reads it back through the API.
        """
        created = await client.post(TRIPS, headers=user_token, json=paris_trip_payload)
        trip_id = created.json()["id"]

        from beanie import PydanticObjectId

        result = await database["trips"].update_one(
            {"_id": PydanticObjectId(trip_id)},
            {"$unset": {"constraints.optimizer_algorithm": ""}},
        )
        assert result.modified_count == 1, "the field should have been there to remove"

        fetched = await client.get(f"{TRIPS}/{trip_id}", headers=user_token)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["constraints"]["optimizer_algorithm"] is None


@pytest.mark.asyncio
class TestPlanSettingReachesTheSolver:
    """The seam where the trip's saved choice becomes the solver's instruction.

    Without this, the field would persist and display perfectly while every plan
    quietly ran the environment default — the kind of bug no other test notices,
    because the itinerary produced is still valid, just not the one asked for.
    """

    async def _capture_request(
        self, monkeypatch: pytest.MonkeyPatch, algorithm: str | None
    ) -> OptimizationRequest:
        from app.ai import pipeline
        from app.models.base import GeoPoint
        from app.models.enums import PlaceCategory
        from app.models.place import Place
        from app.models.trip import Destination, Trip, TripConstraints

        start = date.today() + timedelta(days=14)
        trip = Trip(
            user_id=None,  # type: ignore[arg-type]
            title="Precedence probe",
            destination=Destination(
                city="Paris",
                country="France",
                location=GeoPoint.from_lat_lng(48.8566, 2.3522),
                timezone="Europe/Paris",
            ),
            start_date=start,
            end_date=start + timedelta(days=1),
            constraints=TripConstraints(optimizer_algorithm=algorithm),  # type: ignore[arg-type]
        )

        candidates = [
            Place(
                name=f"Sight {i}",
                slug=f"sight-{i}",
                city="Paris",
                country="France",
                category=PlaceCategory.LANDMARK,
                location=GeoPoint.from_lat_lng(48.85 + i / 100, 2.34 + i / 100),
                visit_duration_minutes=60,
            )
            for i in range(4)
        ]

        captured: dict[str, OptimizationRequest] = {}

        def fake_optimize(request: OptimizationRequest) -> Any:
            captured["request"] = request
            # Enough of a result to let optimize_schedule finish; what it plans
            # is irrelevant here, only what it was asked to plan with.
            from app.optimization.schemas import DaySchedule, OptimizationResult

            return OptimizationResult(
                days=[DaySchedule(day_index=0)], status="stubbed", engine="stub"
            )

        monkeypatch.setattr(pipeline, "optimize", fake_optimize)

        state = pipeline.PlanningState(trip=trip, candidates=candidates)
        with pytest.raises(Exception):  # noqa: B017 - a stub plan is infeasible
            await pipeline.optimize_schedule(state)

        assert "request" in captured, "optimize was never called"
        return captured["request"]

    async def test_the_trip_choice_arrives_on_the_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        request = await self._capture_request(monkeypatch, "nearest_neighbor")
        assert request.algorithm == "nearest_neighbor"

    async def test_an_unset_choice_leaves_the_request_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`None` must stay `None`, so the environment still gets its say."""
        request = await self._capture_request(monkeypatch, None)
        assert request.algorithm is None
