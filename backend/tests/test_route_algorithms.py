"""Route-optimisation algorithms: correctness, comparability and edge cases.

The central claim of ``app.optimization.algorithms`` is that every algorithm is
scored by one evaluator that matches the OR-Tools model, so their objectives can be
compared. The first test class checks that claim against OR-Tools itself; the rest
check that each algorithm returns a *valid* plan (no solver may return a schedule
that breaks opening hours, daily limits or the cap on places), that the guaranteed
relationships between algorithms hold, and that results are reproducible.
"""

from __future__ import annotations

import itertools
import random
from datetime import date, timedelta

import pytest

from app.core.config import OptimizerAlgorithm as A
from app.core.config import settings
from app.models.enums import TransportMode
from app.optimization import engine
from app.optimization.algorithms import (
    ALGORITHM_INFO,
    NATIVE_ALGORITHMS,
    RoutingProblem,
    objective_of,
    solve,
    solve_portfolio,
)
from app.optimization.distance import Coordinate
from app.optimization.schemas import (
    DayConfig,
    OptimizationPlace,
    OptimizationRequest,
    OptimizationResult,
)


def make_request(
    n: int = 14,
    days: int = 2,
    seed: int = 1,
    *,
    windows: bool = True,
    cap: int = 5,
    mandatory: int = 0,
    mode: TransportMode = TransportMode.WALKING,
    spread: float = 0.03,
    max_km: float = 200.0,
    time_limit: int = 1,
) -> OptimizationRequest:
    """A reproducible random itinerary around central Paris."""
    rng = random.Random(seed)
    lat0, lng0 = 48.8566, 2.3522
    places = []
    for i in range(n):
        opening: dict[int, tuple[int, int]] = {}
        always_open = True
        if windows and rng.random() < 0.5:
            always_open = False
            opens = rng.choice([480, 540, 600, 720])
            closes = min(opens + rng.choice([360, 480, 600]), 1380)
            for weekday in range(7):
                if rng.random() < 0.85:
                    opening[weekday] = (opens, closes)
        places.append(
            OptimizationPlace(
                ref=f"p{i}",
                name=f"Place {i}",
                coordinate=Coordinate(
                    lat0 + rng.uniform(-spread, spread), lng0 + rng.uniform(-spread, spread)
                ),
                visit_minutes=rng.choice([30, 45, 60, 90, 120]),
                score=round(rng.uniform(0.2, 1.0), 2),
                mandatory=i < mandatory,
                opening=opening,
                always_open=always_open,
            )
        )
    first = date(2026, 10, 5)
    day_configs = [
        DayConfig(
            index=d,
            day_date=first + timedelta(days=d),
            weekday=(first + timedelta(days=d)).weekday(),
            start_minutes=540,
            end_minutes=1260,
        )
        for d in range(days)
    ]
    return OptimizationRequest(
        places=places,
        days=day_configs,
        origin=Coordinate(lat0, lng0),
        mode=mode,
        max_places_per_day=cap,
        max_daily_distance_km=max_km,
        time_limit_seconds=time_limit,
    )


def assert_valid(request: OptimizationRequest, result: OptimizationResult) -> None:
    """A plan must obey every constraint the OR-Tools model enforces."""
    problem = RoutingProblem(request)
    n = len(request.places)

    scheduled = [stop.place_index for day in result.days for stop in day.stops]
    assert len(scheduled) == len(set(scheduled)), "a place was scheduled twice"
    assert set(scheduled).isdisjoint(result.dropped), (
        "a place is both scheduled and dropped"
    )
    assert set(scheduled) | set(result.dropped) == set(range(n)), "a place went missing"

    for d, day in enumerate(result.days):
        assert len(day.stops) <= request.max_places_per_day
        clock = request.days[d].start_minutes
        for stop in day.stops:
            window = problem.win[d][stop.place_index]
            assert window is not None, "scheduled on a day the place is closed"
            assert window[0] <= stop.arrival_minutes <= window[1], "outside opening hours"
            assert stop.arrival_minutes >= clock, "arrived before leaving the previous stop"
            assert stop.departure_minutes == (
                stop.arrival_minutes + request.places[stop.place_index].visit_minutes
            )
            clock = stop.departure_minutes
        if day.stops:
            assert clock <= request.days[d].end_minutes, "ran past the end of the day"

    # The reported objective is the shared evaluator's.
    assert result.objective_value == objective_of(request, result)
    assert result.objective_value < 10**15, "plan is infeasible by the shared evaluator"


@pytest.fixture(scope="module", params=[(10, 2, 5), (18, 3, 4), (28, 3, 6)])
def instance(request: pytest.FixtureRequest) -> OptimizationRequest:
    n, days, cap = request.param
    return make_request(n=n, days=days, cap=cap, seed=n)


# --------------------------------------------------------------------------- #
# The evaluator is the same model as OR-Tools
# --------------------------------------------------------------------------- #


class TestEvaluatorMatchesORTools:
    @pytest.mark.parametrize("seed", [1, 2, 3, 4])
    def test_objective_is_identical(self, seed: int) -> None:
        pytest.importorskip("ortools")
        req = make_request(n=12, days=2, seed=seed, cap=5, mandatory=1 if seed % 2 else 0)
        result = engine._solve_with_ortools(req)
        assert result is not None

        problem = RoutingProblem(req)
        mine = problem.objective(problem.from_result(result))
        assert mine < 10**15, "OR-Tools plan judged infeasible by the shared evaluator"
        assert mine == result.objective_value


# --------------------------------------------------------------------------- #
# Every algorithm returns a valid plan
# --------------------------------------------------------------------------- #


class TestPlanValidity:
    @pytest.mark.parametrize("algorithm", NATIVE_ALGORITHMS, ids=lambda a: a.value)
    def test_plan_is_valid(self, algorithm: A, instance: OptimizationRequest) -> None:
        result = solve(instance, algorithm)
        assert_valid(instance, result)
        assert result.engine == algorithm.value
        assert result.scheduled_count > 0

    @pytest.mark.parametrize("algorithm", NATIVE_ALGORITHMS, ids=lambda a: a.value)
    def test_mandatory_places_are_served_when_possible(self, algorithm: A) -> None:
        req = make_request(n=14, days=3, seed=7, windows=False, cap=5, mandatory=3)
        result = solve(req, algorithm)
        assert_valid(req, result)
        served = {s.place_index for day in result.days for s in day.stops}
        assert {0, 1, 2} <= served

    @pytest.mark.parametrize("algorithm", NATIVE_ALGORITHMS, ids=lambda a: a.value)
    def test_honours_the_daily_distance_cap(self, algorithm: A) -> None:
        req = make_request(n=14, days=2, seed=3, windows=False, spread=0.12, max_km=6.0)
        result = solve(req, algorithm)
        assert_valid(req, result)
        problem = RoutingProblem(req)
        for d, route in enumerate(problem.from_result(result).routes):
            evaluated = problem.route_eval(d, route)
            assert evaluated is not None
            assert evaluated[1] <= 6.0 + 1e-6

    @pytest.mark.parametrize("algorithm", NATIVE_ALGORITHMS, ids=lambda a: a.value)
    def test_driving_mode_is_valid(self, algorithm: A) -> None:
        req = make_request(n=16, days=2, seed=5, mode=TransportMode.DRIVING, spread=0.2)
        assert_valid(req, solve(req, algorithm))


# --------------------------------------------------------------------------- #
# Relationships between algorithms that hold by construction
# --------------------------------------------------------------------------- #


class TestGuaranteedRelationships:
    def test_local_search_never_worse_than_its_starting_point(
        self, instance: OptimizationRequest
    ) -> None:
        ci = objective_of(instance, solve(instance, A.CHEAPEST_INSERTION))
        ls = objective_of(instance, solve(instance, A.LOCAL_SEARCH))
        assert ls <= ci

    def test_annealing_never_worse_than_local_search(
        self, instance: OptimizationRequest
    ) -> None:
        ls = objective_of(instance, solve(instance, A.LOCAL_SEARCH))
        sa = objective_of(instance, solve(instance, A.SIMULATED_ANNEALING))
        assert sa <= ls

    def test_insertion_beats_nearest_neighbour_overall(self) -> None:
        """Not guaranteed per instance, but decisively true in aggregate."""
        nn = ci = 0
        for seed in range(1, 9):
            req = make_request(n=20, days=3, seed=seed, cap=5)
            nn += objective_of(req, solve(req, A.NEAREST_NEIGHBOR))
            ci += objective_of(req, solve(req, A.CHEAPEST_INSERTION))
        assert ci < nn


class TestAuto:
    def test_auto_is_at_least_as_good_as_every_native_algorithm(
        self, instance: OptimizationRequest
    ) -> None:
        best_native = min(
            objective_of(instance, solve(instance, a)) for a in NATIVE_ALGORITHMS
        )
        auto = solve_portfolio(instance, None)
        assert_valid(instance, auto)
        assert auto.objective_value <= best_native
        assert auto.engine.startswith("auto:")

    def test_auto_includes_ortools_when_given(self) -> None:
        pytest.importorskip("ortools")
        req = make_request(n=12, days=2, seed=2)
        auto = solve_portfolio(req, engine._solve_with_ortools)
        assert_valid(req, auto)
        assert auto.status == f"best_of_{len(NATIVE_ALGORITHMS) + 1}"


# --------------------------------------------------------------------------- #
# Reproducibility
# --------------------------------------------------------------------------- #


def _signature(result: OptimizationResult) -> list[list[int]]:
    return [[s.place_index for s in day.stops] for day in result.days]


class TestDeterminism:
    @pytest.mark.parametrize("algorithm", NATIVE_ALGORITHMS, ids=lambda a: a.value)
    def test_same_request_gives_same_plan(self, algorithm: A) -> None:
        req = make_request(n=18, days=3, seed=11)
        assert _signature(solve(req, algorithm)) == _signature(solve(req, algorithm))

    def test_the_seed_changes_randomised_runs(self) -> None:
        """Different seeds explore differently (checked over several to avoid a fluke)."""
        outcomes = set()
        for seed in range(6):
            req = make_request(n=24, days=3, seed=11)
            req.seed = seed
            outcomes.add(str(_signature(solve(req, A.GENETIC))))
        assert len(outcomes) > 1


# --------------------------------------------------------------------------- #
# Optimality on a problem small enough to brute-force
# --------------------------------------------------------------------------- #


def _brute_force_optimum(req: OptimizationRequest) -> int:
    """Best objective over every ordered subset of places on a single day."""
    problem = RoutingProblem(req)
    n = problem.n
    best = sum(problem.penalty)  # leaving everything out
    for size in range(1, n + 1):
        for subset in itertools.permutations(range(n), size):
            evaluated = problem.route_eval(0, list(subset))
            if evaluated is None:
                continue
            left_out = sum(problem.penalty[i] for i in range(n) if i not in subset)
            best = min(best, evaluated[0] + left_out)
    return best


class TestOptimality:
    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
    def test_matches_brute_force_on_small_instances(self, seed: int) -> None:
        req = make_request(n=6, days=1, seed=seed, windows=False, cap=6, spread=0.02)
        optimum = _brute_force_optimum(req)

        sa = objective_of(req, solve(req, A.SIMULATED_ANNEALING))
        ls = objective_of(req, solve(req, A.LOCAL_SEARCH))
        assert sa == optimum, "annealing missed the optimum on a 6-place problem"
        assert ls <= optimum * 1.05, "local search is more than 5% off the optimum"

    def test_ortools_agrees_with_brute_force(self) -> None:
        pytest.importorskip("ortools")
        req = make_request(n=6, days=1, seed=2, windows=False, cap=6, spread=0.02)
        result = engine._solve_with_ortools(req)
        assert objective_of(req, result) == _brute_force_optimum(req)


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #


class TestEdgeCases:
    @pytest.mark.parametrize("algorithm", NATIVE_ALGORITHMS, ids=lambda a: a.value)
    def test_place_closed_every_day_is_dropped_for_free(self, algorithm: A) -> None:
        req = make_request(n=8, days=2, seed=4, windows=False)
        req.places[3].always_open = False
        req.places[3].opening = {}
        problem = RoutingProblem(req)
        assert problem.schedulable[3] is False
        assert problem.penalty[3] == 0

        result = solve(req, algorithm)
        assert 3 in result.dropped
        assert_valid(req, result)

    @pytest.mark.parametrize("algorithm", NATIVE_ALGORITHMS, ids=lambda a: a.value)
    def test_single_place(self, algorithm: A) -> None:
        req = make_request(n=1, days=1, seed=1, windows=False)
        result = solve(req, algorithm)
        assert result.scheduled_count == 1
        assert_valid(req, result)

    @pytest.mark.parametrize("algorithm", NATIVE_ALGORITHMS, ids=lambda a: a.value)
    def test_more_places_than_fit_drops_the_rest(self, algorithm: A) -> None:
        req = make_request(n=20, days=1, seed=6, windows=False, cap=3)
        result = solve(req, algorithm)
        assert result.scheduled_count <= 3
        assert len(result.dropped) >= 17
        assert_valid(req, result)

    @pytest.mark.parametrize("algorithm", NATIVE_ALGORITHMS, ids=lambda a: a.value)
    def test_impossible_mandatory_place_does_not_crash(self, algorithm: A) -> None:
        req = make_request(n=6, days=1, seed=1, windows=False, mandatory=6, cap=3)
        result = solve(req, algorithm)
        assert result.scheduled_count <= 3
        assert_valid(req, result)

    @pytest.mark.parametrize("algorithm", NATIVE_ALGORITHMS, ids=lambda a: a.value)
    def test_a_tiny_time_budget_still_returns_a_valid_plan(self, algorithm: A) -> None:
        req = make_request(n=30, days=3, seed=9)
        result = solve(req, algorithm, time_budget=0.05)
        assert_valid(req, result)

    def test_rejects_non_native_algorithms(self) -> None:
        req = make_request(n=4, days=1)
        for algorithm in (A.ORTOOLS, A.AUTO):
            with pytest.raises(ValueError):
                solve(req, algorithm)

    def test_every_algorithm_is_described(self) -> None:
        assert set(ALGORITHM_INFO) == set(A)
        for info in ALGORITHM_INFO.values():
            assert info["label"] and info["description"]


# --------------------------------------------------------------------------- #
# Engine dispatch and comparison
# --------------------------------------------------------------------------- #


class TestEngineDispatch:
    @pytest.mark.parametrize("algorithm", NATIVE_ALGORITHMS, ids=lambda a: a.value)
    def test_optimize_runs_the_requested_algorithm(self, algorithm: A) -> None:
        req = make_request(n=12, days=2, seed=3)
        result = engine.optimize(req, algorithm)
        assert result.engine == algorithm.value
        assert result.distance_before_km is not None

    def test_request_field_selects_the_algorithm(self) -> None:
        req = make_request(n=12, days=2, seed=3)
        req.algorithm = "local_search"
        assert engine.optimize(req).engine == "local_search"

    def test_explicit_argument_beats_the_request(self) -> None:
        req = make_request(n=12, days=2, seed=3)
        req.algorithm = "local_search"
        assert engine.optimize(req, A.GENETIC).engine == "genetic"

    def test_settings_default_is_used_when_nothing_is_specified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "optimizer_algorithm", A.CHEAPEST_INSERTION)
        assert (
            engine.optimize(make_request(n=10, days=2, seed=3)).engine
            == "cheapest_insertion"
        )

    def test_default_stays_ortools(self) -> None:
        pytest.importorskip("ortools")
        assert settings.optimizer_algorithm is A.ORTOOLS
        assert engine.optimize(make_request(n=10, days=2, seed=3)).engine.startswith(
            "ortools"
        )

    def test_unknown_algorithm_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            engine.optimize(make_request(n=6, days=1), "quantum_annealing")

    def test_auto_through_the_engine(self) -> None:
        pytest.importorskip("ortools")
        result = engine.optimize(make_request(n=12, days=2, seed=3), A.AUTO)
        assert result.engine.startswith("auto:")

    def test_empty_input_is_unchanged(self) -> None:
        req = make_request(n=0, days=1)
        assert engine.optimize(req, A.GENETIC).status == "empty_input"


class TestCompare:
    def test_returns_one_valid_result_per_algorithm(self) -> None:
        pytest.importorskip("ortools")
        req = make_request(n=14, days=2, seed=5)
        results = engine.compare_algorithms(req)
        assert [r.engine for r in results] == [
            "nearest_neighbor",
            "cheapest_insertion",
            "local_search",
            "simulated_annealing",
            "genetic",
            "ortools",
        ]
        for result in results:
            assert_valid(req, result)

    def test_objectives_are_on_one_scale(self) -> None:
        pytest.importorskip("ortools")
        req = make_request(n=14, days=2, seed=5)
        results = engine.compare_algorithms(req)
        best = min(r.objective_value for r in results)
        # The strongest solvers land close together; nearest neighbour does not.
        by_name = {r.engine: r.objective_value for r in results}
        assert by_name["ortools"] <= best * 1.02
        assert by_name["simulated_annealing"] <= best * 1.10
        assert by_name["nearest_neighbor"] >= best

    def test_a_subset_can_be_requested(self) -> None:
        req = make_request(n=10, days=2, seed=1)
        results = engine.compare_algorithms(req, [A.LOCAL_SEARCH, A.GENETIC])
        assert [r.engine for r in results] == ["local_search", "genetic"]

    def test_auto_is_skipped_in_a_comparison(self) -> None:
        req = make_request(n=10, days=2, seed=1)
        assert (
            engine.compare_algorithms(req, [A.AUTO, A.LOCAL_SEARCH])[0].engine
            == "local_search"
        )


# --------------------------------------------------------------------------- #
# Service layer, against an in-memory database
# --------------------------------------------------------------------------- #


@pytest.fixture
async def london_ids(monkeypatch: pytest.MonkeyPatch):
    from beanie import init_beanie
    from mongomock_motor import AsyncMongoMockClient

    from app.db import world_seed
    from app.models.place import Place

    database = AsyncMongoMockClient()["route_service_test"]
    await init_beanie(database=database, document_models=[Place])
    monkeypatch.setattr(world_seed, "get_database", lambda: database)
    await world_seed.seed_world_places(embed=False)
    places = await Place.find(Place.city == "London").to_list()
    return [p.id for p in places[:12]]


class TestRouteService:
    async def test_optimize_with_a_chosen_algorithm(self, london_ids) -> None:
        from app.schemas.itinerary import OptimizeRouteRequest
        from app.services import itinerary_service

        response = await itinerary_service.optimize_places(
            OptimizeRouteRequest(
                place_ids=london_ids,
                start_latitude=51.5074,
                start_longitude=-0.1278,
                days=2,
                max_places_per_day=6,
                algorithm=A.LOCAL_SEARCH,
            )
        )
        assert response.engine == "local_search"
        assert response.objective is not None
        assert len(response.stops) + len(response.dropped_place_ids) == len(london_ids)

    async def test_compare_ranks_algorithms(self, london_ids) -> None:
        pytest.importorskip("ortools")
        from app.schemas.itinerary import CompareRouteRequest
        from app.services import itinerary_service

        response = await itinerary_service.compare_route_algorithms(
            CompareRouteRequest(
                place_ids=london_ids,
                start_latitude=51.5074,
                start_longitude=-0.1278,
                days=2,
                max_places_per_day=6,
                time_limit_seconds=1,
            )
        )
        assert len(response.runs) == 6
        assert sum(run.is_best for run in response.runs) == 1
        best = next(run for run in response.runs if run.is_best)
        assert best.gap_percent == 0
        assert best.algorithm == response.best_algorithm
        assert all(run.gap_percent >= 0 for run in response.runs)
        assert all(run.label for run in response.runs)

    async def test_compare_subset(self, london_ids) -> None:
        from app.schemas.itinerary import CompareRouteRequest
        from app.services import itinerary_service

        response = await itinerary_service.compare_route_algorithms(
            CompareRouteRequest(
                place_ids=london_ids,
                start_latitude=51.5074,
                start_longitude=-0.1278,
                days=2,
                algorithms=[A.NEAREST_NEIGHBOR, A.LOCAL_SEARCH],
            )
        )
        assert [run.algorithm for run in response.runs] == [
            "nearest_neighbor",
            "local_search",
        ]
        assert response.best_algorithm == "local_search"

    async def test_too_few_places_is_a_not_found(self, london_ids) -> None:
        from app.core.exceptions import NotFoundError
        from app.schemas.itinerary import CompareRouteRequest
        from app.services import itinerary_service

        with pytest.raises(NotFoundError):
            await itinerary_service.compare_route_algorithms(
                CompareRouteRequest(
                    place_ids=[london_ids[0], london_ids[0].__class__()],
                    start_latitude=51.5,
                    start_longitude=-0.1,
                )
            )

    def test_algorithm_listing(self) -> None:
        from app.services import itinerary_service

        keys = {info.key for info in itinerary_service.list_optimizer_algorithms()}
        assert keys == {a.value for a in A}


class TestOpeningHoursUseTheRequestedDate:
    """Planning a specific day must check opening hours against that day's weekday."""

    async def test_start_date_decides_which_places_are_open(self, london_ids) -> None:
        from datetime import date

        from app.models.place import OpeningHours, Place
        from app.schemas.itinerary import CompareRouteRequest
        from app.services import itinerary_service

        places = [await Place.get(pid) for pid in london_ids[:4]]
        # Open on Tuesdays only (weekday 1).
        places[0].opening_hours = [
            OpeningHours(day_of_week=1, opens="09:00", closes="18:00")
        ]
        monday, tuesday = date(2026, 10, 5), date(2026, 10, 6)
        assert (monday.weekday(), tuesday.weekday()) == (0, 1)

        def build(day: date) -> RoutingProblem:
            payload = CompareRouteRequest(
                place_ids=london_ids[:4],
                start_latitude=51.5074,
                start_longitude=-0.1278,
                start_date=day,
            )
            return RoutingProblem(itinerary_service._build_route_request(payload, places))

        assert build(monday).schedulable[0] is False
        assert build(tuesday).schedulable[0] is True
        # The other places carry no hours of their own and stay available.
        assert all(build(monday).schedulable[1:])

    async def test_defaults_to_today(self, london_ids) -> None:
        from datetime import date

        from app.models.place import Place
        from app.schemas.itinerary import CompareRouteRequest
        from app.services import itinerary_service

        places = [await Place.get(pid) for pid in london_ids[:3]]
        payload = CompareRouteRequest(
            place_ids=london_ids[:3], start_latitude=51.5, start_longitude=-0.1
        )
        request = itinerary_service._build_route_request(payload, places)
        assert request.days[0].day_date == date.today()
