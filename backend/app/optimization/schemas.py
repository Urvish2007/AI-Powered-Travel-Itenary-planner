"""Data contracts for the optimisation engine.

These are deliberately plain dataclasses rather than Beanie documents or API
schemas: the optimiser knows nothing about MongoDB or HTTP, which keeps it
unit-testable in isolation and swappable behind the service layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.models.enums import TransportMode
from app.optimization.distance import Coordinate, TravelMatrix, estimated_matrix

# Minutes in a day. The multi-day model lays every day out on one global
# timeline, so day ``d`` occupies ``[d * MINUTES_PER_DAY, (d + 1) * MINUTES_PER_DAY)``.
MINUTES_PER_DAY = 24 * 60


@dataclass(slots=True)
class OptimizationPlace:
    """A candidate place, reduced to what the solver needs."""

    ref: str
    name: str
    coordinate: Coordinate
    visit_minutes: int = 60
    # Desirability in [0, 1]; drives how expensive it is to leave the place out.
    score: float = 0.5
    cost: float = 0.0
    mandatory: bool = False
    # weekday (Mon=0) -> (opens_minute, closes_minute), both from local midnight.
    opening: dict[int, tuple[int, int]] = field(default_factory=dict)
    # True when the place has no recorded hours and is assumed always available.
    always_open: bool = True
    category: str = "other"

    def window_for(self, weekday: int) -> tuple[int, int] | None:
        """Opening window on ``weekday``, or None when closed that day."""
        if self.always_open:
            return (0, MINUTES_PER_DAY)
        return self.opening.get(weekday)


@dataclass(slots=True)
class DayConfig:
    """One day of the trip, as the solver sees it."""

    index: int
    day_date: date
    weekday: int
    start_minutes: int
    end_minutes: int

    @property
    def length_minutes(self) -> int:
        return self.end_minutes - self.start_minutes


@dataclass(slots=True)
class OptimizationRequest:
    places: list[OptimizationPlace]
    days: list[DayConfig]
    origin: Coordinate
    mode: TransportMode = TransportMode.WALKING
    max_places_per_day: int = 5
    max_daily_distance_km: float = 25.0
    time_limit_seconds: int = 10
    return_to_origin: bool = False
    # Slack allows the solver to wait for a place to open rather than declaring
    # the route infeasible.
    max_wait_minutes: int = 180
    # Which algorithm to run; ``None`` means "use settings.optimizer_algorithm".
    algorithm: str | None = None
    # Seeds the randomised algorithms so a given request always yields the same plan.
    seed: int = 42
    # Travel between node 0 (the origin) and node ``i + 1`` (place ``i``). ``None``
    # means "estimate it"; a measured matrix is fetched by the service layer,
    # since this package does no I/O.
    matrix: TravelMatrix | None = None

    def travel_matrix(self) -> TravelMatrix:
        """The matrix every solver and the evaluator read legs from.

        Built once and kept, so a comparison of six algorithms prices every one
        of them against the same figures.

        Raises:
            ValueError: when a supplied matrix does not cover origin + places.
        """
        expected = len(self.places) + 1
        if self.matrix is None:
            coords = [self.origin] + [p.coordinate for p in self.places]
            self.matrix = estimated_matrix(coords, self.mode)
        elif self.matrix.size != expected:
            raise ValueError(
                f"travel matrix covers {self.matrix.size} points; expected {expected}"
            )
        return self.matrix


@dataclass(slots=True)
class ScheduledStop:
    place_index: int
    arrival_minutes: int
    departure_minutes: int
    travel_km: float = 0.0
    travel_minutes: int = 0
    mode: TransportMode = TransportMode.WALKING


@dataclass(slots=True)
class DaySchedule:
    day_index: int
    stops: list[ScheduledStop] = field(default_factory=list)
    total_distance_km: float = 0.0
    total_travel_minutes: int = 0


@dataclass(slots=True)
class OptimizationResult:
    days: list[DaySchedule]
    dropped: list[int] = field(default_factory=list)
    status: str = "not_run"
    objective_value: int | None = None
    duration_seconds: float = 0.0
    engine: str = "ortools"
    distance_before_km: float | None = None

    @property
    def scheduled_count(self) -> int:
        return sum(len(day.stops) for day in self.days)

    @property
    def total_distance_km(self) -> float:
        return round(sum(day.total_distance_km for day in self.days), 3)

    @property
    def feasible(self) -> bool:
        return self.scheduled_count > 0
