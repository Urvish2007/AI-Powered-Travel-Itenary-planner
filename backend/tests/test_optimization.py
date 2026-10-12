"""Unit tests for the optimisation engine. No database required."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.models.enums import TransportMode
from app.optimization.distance import (
    Coordinate,
    build_travel_matrix,
    effective_mode,
    haversine_km,
    haversine_matrix_km,
    travel_minutes,
)
from app.optimization.engine import ORTOOLS_AVAILABLE, _solve_greedy, optimize
from app.optimization.schemas import (
    DayConfig,
    OptimizationPlace,
    OptimizationRequest,
)

PARIS = Coordinate(48.8566, 2.3522)
LONDON = Coordinate(51.5074, -0.1278)
EIFFEL = Coordinate(48.8584, 2.2945)
LOUVRE = Coordinate(48.8606, 2.3376)


class TestDistance:
    def test_haversine_matches_known_distance(self) -> None:
        # Paris to London is about 344 km great-circle.
        assert 340 < haversine_km(PARIS, LONDON) < 350

    def test_haversine_is_zero_for_identical_points(self) -> None:
        assert haversine_km(PARIS, PARIS) == 0.0

    def test_haversine_is_symmetric(self) -> None:
        assert haversine_km(PARIS, LONDON) == pytest.approx(haversine_km(LONDON, PARIS))

    def test_matrix_matches_pairwise_computation(self) -> None:
        coords = [PARIS, LONDON, EIFFEL, LOUVRE]
        matrix = haversine_matrix_km(coords)

        assert matrix.shape == (4, 4)
        for i in range(4):
            assert matrix[i][i] == pytest.approx(0.0, abs=1e-9)
            for j in range(4):
                assert matrix[i][j] == pytest.approx(
                    haversine_km(coords[i], coords[j]), rel=1e-6
                )

    def test_empty_matrix(self) -> None:
        assert haversine_matrix_km([]).shape == (0, 0)

    def test_travel_time_grows_with_distance(self) -> None:
        near = travel_minutes(1.0, TransportMode.WALKING)
        far = travel_minutes(5.0, TransportMode.WALKING)
        assert 0 < near < far

    def test_walking_is_slower_than_driving_over_distance(self) -> None:
        assert travel_minutes(10.0, TransportMode.WALKING) > travel_minutes(
            10.0, TransportMode.DRIVING
        )

    def test_zero_distance_costs_no_time(self) -> None:
        assert travel_minutes(0.0, TransportMode.WALKING) == 0

    def test_long_walks_fall_back_to_transit(self) -> None:
        assert effective_mode(6.0, TransportMode.WALKING) is TransportMode.TRANSIT
        assert effective_mode(1.0, TransportMode.WALKING) is TransportMode.WALKING

    def test_very_short_drives_become_walks(self) -> None:
        assert effective_mode(0.3, TransportMode.DRIVING) is TransportMode.WALKING

    def test_travel_matrix_shapes_and_diagonal(self) -> None:
        distances, durations = build_travel_matrix(
            [EIFFEL, LOUVRE, PARIS], TransportMode.WALKING
        )
        assert distances.shape == durations.shape == (3, 3)
        for i in range(3):
            assert durations[i][i] == 0


def _place(
    ref: str, coord: Coordinate, *, minutes: int = 60, score: float = 0.8, **kwargs
) -> OptimizationPlace:
    return OptimizationPlace(
        ref=ref, name=ref, coordinate=coord, visit_minutes=minutes, score=score, **kwargs
    )


def _days(count: int = 1, start: str = 540, end: str = 1200) -> list[DayConfig]:
    today = date.today()
    return [
        DayConfig(
            index=i,
            day_date=today + timedelta(days=i),
            weekday=(today + timedelta(days=i)).weekday(),
            start_minutes=start,  # type: ignore[arg-type]
            end_minutes=end,  # type: ignore[arg-type]
        )
        for i in range(count)
    ]


class TestOptimizer:
    def test_empty_input_returns_empty_days(self) -> None:
        result = optimize(OptimizationRequest(places=[], days=_days(2), origin=PARIS))
        assert result.status == "empty_input"
        assert len(result.days) == 2
        assert result.scheduled_count == 0

    def test_schedules_places_within_the_day_window(self) -> None:
        places = [
            _place("eiffel", EIFFEL, minutes=90),
            _place("louvre", LOUVRE, minutes=120),
            _place("centre", PARIS, minutes=60),
        ]
        result = optimize(
            OptimizationRequest(
                places=places,
                days=_days(1),
                origin=PARIS,
                max_places_per_day=3,
                time_limit_seconds=3,
            )
        )
        assert result.feasible
        for day in result.days:
            for stop in day.stops:
                assert stop.arrival_minutes >= 540
                assert stop.departure_minutes <= 1200

    def test_respects_places_per_day_cap(self) -> None:
        places = [
            _place(f"p{i}", Coordinate(48.85 + i * 0.004, 2.34 + i * 0.004), minutes=45)
            for i in range(10)
        ]
        result = optimize(
            OptimizationRequest(
                places=places,
                days=_days(2),
                origin=PARIS,
                max_places_per_day=2,
                time_limit_seconds=3,
            )
        )
        for day in result.days:
            assert len(day.stops) <= 2

    def test_stops_never_overlap_in_time(self) -> None:
        places = [
            _place(f"p{i}", Coordinate(48.85 + i * 0.006, 2.33 + i * 0.006), minutes=60)
            for i in range(6)
        ]
        result = optimize(
            OptimizationRequest(
                places=places,
                days=_days(2),
                origin=PARIS,
                max_places_per_day=3,
                time_limit_seconds=4,
            )
        )
        for day in result.days:
            previous_end = -1
            for stop in sorted(day.stops, key=lambda s: s.arrival_minutes):
                assert stop.arrival_minutes >= previous_end
                previous_end = stop.departure_minutes

    def test_a_place_is_scheduled_at_most_once(self) -> None:
        places = [
            _place(f"p{i}", Coordinate(48.85 + i * 0.005, 2.34 + i * 0.005))
            for i in range(8)
        ]
        result = optimize(
            OptimizationRequest(
                places=places,
                days=_days(3),
                origin=PARIS,
                max_places_per_day=3,
                time_limit_seconds=4,
            )
        )
        seen = [stop.place_index for day in result.days for stop in day.stops]
        assert len(seen) == len(set(seen))

    def test_closed_places_are_never_scheduled(self) -> None:
        """A place open on no day of the trip must be dropped, not squeezed in."""
        days = _days(1)
        # Open only on a weekday that is not the single trip day.
        wrong_day = (days[0].weekday + 3) % 7
        closed = OptimizationPlace(
            ref="closed",
            name="closed",
            coordinate=LOUVRE,
            visit_minutes=60,
            score=0.99,
            opening={wrong_day: (540, 1080)},
            always_open=False,
        )
        open_place = _place("open", EIFFEL, minutes=60)

        result = optimize(
            OptimizationRequest(
                places=[closed, open_place],
                days=days,
                origin=PARIS,
                max_places_per_day=3,
                time_limit_seconds=3,
            )
        )
        scheduled = {stop.place_index for day in result.days for stop in day.stops}
        assert 0 not in scheduled, "a place closed all trip was scheduled anyway"

    def test_opening_hours_are_honoured(self) -> None:
        days = _days(1)
        late = OptimizationPlace(
            ref="late",
            name="late",
            coordinate=LOUVRE,
            visit_minutes=60,
            score=0.9,
            # Opens at 16:00 only.
            opening={days[0].weekday: (960, 1140)},
            always_open=False,
        )
        result = optimize(
            OptimizationRequest(
                places=[late, _place("other", EIFFEL)],
                days=days,
                origin=PARIS,
                max_places_per_day=2,
                time_limit_seconds=3,
            )
        )
        for day in result.days:
            for stop in day.stops:
                if stop.place_index == 0:
                    assert stop.arrival_minutes >= 960
                    assert stop.departure_minutes <= 1140

    def test_mandatory_places_are_preferred_over_optional_ones(self) -> None:
        mandatory = _place("must", LOUVRE, minutes=60, score=0.1, mandatory=True)
        optional = [
            _place(f"opt{i}", Coordinate(48.86 + i * 0.003, 2.34 + i * 0.003), score=0.99)
            for i in range(5)
        ]
        result = optimize(
            OptimizationRequest(
                places=[mandatory, *optional],
                days=_days(1),
                origin=PARIS,
                max_places_per_day=2,
                time_limit_seconds=4,
            )
        )
        scheduled = {stop.place_index for day in result.days for stop in day.stops}
        assert 0 in scheduled, "the mandatory place was dropped in favour of optional ones"

    def test_optimised_route_beats_the_naive_ordering(self) -> None:
        """The whole point of the solver: a shorter route than the input order."""
        # Deliberately zig-zagged input ordering.
        coords = [
            Coordinate(48.90, 2.30),
            Coordinate(48.84, 2.40),
            Coordinate(48.89, 2.31),
            Coordinate(48.85, 2.39),
            Coordinate(48.88, 2.32),
            Coordinate(48.86, 2.38),
        ]
        places = [_place(f"p{i}", c, minutes=30) for i, c in enumerate(coords)]
        result = optimize(
            OptimizationRequest(
                places=places,
                days=_days(1),
                origin=PARIS,
                max_places_per_day=6,
                max_daily_distance_km=100.0,
                time_limit_seconds=5,
            )
        )
        assert result.feasible
        assert result.distance_before_km is not None
        assert result.total_distance_km <= result.distance_before_km + 1e-6

    def test_multi_day_spreads_across_days(self) -> None:
        places = [
            _place(f"p{i}", Coordinate(48.85 + i * 0.004, 2.34 + i * 0.004), minutes=90)
            for i in range(9)
        ]
        result = optimize(
            OptimizationRequest(
                places=places,
                days=_days(3),
                origin=PARIS,
                max_places_per_day=3,
                time_limit_seconds=5,
            )
        )
        used_days = [d for d in result.days if d.stops]
        assert len(used_days) >= 2, "everything was crammed into a single day"


class TestGreedyFallback:
    """The greedy engine must satisfy the same hard constraints as OR-Tools."""

    def _request(self, **overrides) -> OptimizationRequest:
        places = [
            _place(f"p{i}", Coordinate(48.85 + i * 0.005, 2.34 + i * 0.005), minutes=60)
            for i in range(7)
        ]
        defaults = {
            "places": places,
            "days": _days(2),
            "origin": PARIS,
            "max_places_per_day": 3,
            "time_limit_seconds": 2,
        }
        defaults.update(overrides)
        return OptimizationRequest(**defaults)  # type: ignore[arg-type]

    def test_produces_a_feasible_plan(self) -> None:
        result = _solve_greedy(self._request())
        assert result.engine == "greedy"
        assert result.feasible

    def test_respects_pace_and_day_window(self) -> None:
        result = _solve_greedy(self._request())
        for day in result.days:
            assert len(day.stops) <= 3
            for stop in day.stops:
                assert stop.arrival_minutes >= 540
                assert stop.departure_minutes <= 1200

    def test_does_not_double_book_a_place(self) -> None:
        result = _solve_greedy(self._request())
        seen = [s.place_index for d in result.days for s in d.stops]
        assert len(seen) == len(set(seen))


@pytest.mark.skipif(not ORTOOLS_AVAILABLE, reason="OR-Tools is not installed")
def test_ortools_is_the_engine_actually_used() -> None:
    result = optimize(
        OptimizationRequest(
            places=[_place("a", EIFFEL), _place("b", LOUVRE)],
            days=_days(1),
            origin=PARIS,
            time_limit_seconds=2,
        )
    )
    assert result.engine == "ortools"
