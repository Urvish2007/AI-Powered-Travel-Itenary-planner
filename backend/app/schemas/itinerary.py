"""Itinerary request and response bodies."""

from __future__ import annotations

from datetime import date, datetime

from beanie import PydanticObjectId
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import OptimizerAlgorithm
from app.models.enums import ItineraryStatus
from app.models.itinerary import (
    GenerationMeta,
    ItineraryDay,
    OptimizationReport,
)


class ItineraryPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: PydanticObjectId
    trip_id: PydanticObjectId
    user_id: PydanticObjectId
    version: int
    status: ItineraryStatus
    title: str
    summary: str
    days: list[ItineraryDay]
    total_estimated_cost: float
    currency: str
    total_distance_km: float
    optimization: OptimizationReport
    generation: GenerationMeta
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ItinerarySummary(BaseModel):
    """List-view projection: everything except the day-by-day detail."""

    model_config = ConfigDict(from_attributes=True)

    id: PydanticObjectId
    trip_id: PydanticObjectId
    version: int
    status: ItineraryStatus
    title: str
    total_estimated_cost: float
    currency: str
    total_distance_km: float
    day_count: int
    created_at: datetime


class ReorderDayRequest(BaseModel):
    """Manually reorder one day's activities.

    Times are recomputed from the new order, so the client sends intent (the
    sequence) rather than a schedule it would have to compute itself.
    """

    day_number: int = Field(ge=1)
    activity_order: list[int] = Field(
        min_length=1,
        description="Existing activity indices, in their new order.",
    )


class ApplyRouteRequest(BaseModel):
    """Write a compared route's ordering back onto a day.

    The client sends the order it chose and nothing else it could get wrong: no
    times, no distances, no totals. Those are recomputed here from the stored
    places, because a client that reported its own figures could report any
    figures, and the stored plan has to stay internally consistent.
    """

    place_ids: list[PydanticObjectId] = Field(
        min_length=2,
        max_length=60,
        description=(
            "The day's routable places, in the order to apply. Must be exactly "
            "the set already on the day - no additions, removals or repeats."
        ),
    )
    algorithm: OptimizerAlgorithm = Field(
        description="Which algorithm produced this order, recorded on the day."
    )
    expected_updated_at: datetime = Field(
        description=(
            "The itinerary's `updated_at` as of the comparison. If the stored "
            "itinerary has moved on, the comparison was made against a day that "
            "no longer exists and the request is refused with 409."
        )
    )


class ReoptimizeRequest(BaseModel):
    """Re-run the optimiser over an existing itinerary's places."""

    day_number: int | None = Field(
        default=None,
        ge=1,
        description="Limit re-optimisation to one day; omit to re-run the whole trip.",
    )
    time_limit_seconds: int | None = Field(default=None, ge=1, le=60)


class _RouteInputs(BaseModel):
    """The places and constraints shared by every route-optimisation request."""

    place_ids: list[PydanticObjectId] = Field(min_length=2, max_length=60)
    start_latitude: float = Field(ge=-90, le=90)
    start_longitude: float = Field(ge=-180, le=180)
    day_start_time: str = Field(default="09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    day_end_time: str = Field(default="21:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: int = Field(default=1, ge=1, le=14)
    transport_mode: str = Field(default="walking")
    max_places_per_day: int = Field(default=6, ge=1, le=12)
    start_date: date | None = Field(
        default=None,
        description=(
            "Date of the first day. Opening hours are checked against its weekday, "
            "so set it when planning a specific day. Defaults to today."
        ),
    )


class OptimizeRouteRequest(_RouteInputs):
    """Optimise an arbitrary set of places, independent of any saved trip.

    Exposed so the frontend can offer a "best order for these stops" tool without
    first creating a trip.
    """

    algorithm: OptimizerAlgorithm | None = Field(
        default=None,
        description=(
            "Which algorithm plans the route. Omit for the server default. "
            "See GET /itineraries/optimizer/algorithms."
        ),
    )
    time_limit_seconds: int = Field(
        default=5, ge=1, le=30, description="Wall-clock budget for the OR-Tools solver."
    )


class CompareRouteRequest(_RouteInputs):
    """Run several algorithms on the same places and compare the plans."""

    algorithms: list[OptimizerAlgorithm] | None = Field(
        default=None,
        max_length=6,
        description="Algorithms to compare. Omit to compare all of them.",
    )
    time_limit_seconds: int = Field(
        default=3, ge=1, le=15, description="Wall-clock budget for the OR-Tools run."
    )


class OptimizedStop(BaseModel):
    place_id: PydanticObjectId
    name: str
    day_number: int
    order: int
    arrival_time: str
    departure_time: str
    travel_km: float
    travel_minutes: int
    mode: str


class OptimizeRouteResponse(BaseModel):
    stops: list[OptimizedStop]
    dropped_place_ids: list[PydanticObjectId] = Field(default_factory=list)
    total_distance_km: float
    distance_before_km: float | None = None
    solver_status: str
    engine: str
    duration_seconds: float
    objective: int | None = Field(
        default=None,
        description="Plan cost in minutes (travel + visits + penalties). Lower is better.",
    )
    estimated: bool = Field(
        default=True,
        description=(
            "True when distances and times are straight-line estimates rather "
            "than measured on the road network."
        ),
    )
    routing_provider: str = Field(
        default="haversine", description="Where travel figures came from."
    )


class AlgorithmRun(BaseModel):
    """One algorithm's plan, with the numbers needed to compare it to the others."""

    algorithm: str
    label: str
    solver_status: str
    scheduled_count: int
    dropped_count: int
    total_distance_km: float
    travel_minutes: int
    objective: int = Field(description="Plan cost in minutes. Lower is better.")
    gap_percent: float = Field(description="How much worse than the best plan, in percent.")
    duration_seconds: float
    is_best: bool
    stops: list[OptimizedStop]
    dropped_place_ids: list[PydanticObjectId] = Field(default_factory=list)


class CompareRouteResponse(BaseModel):
    runs: list[AlgorithmRun]
    best_algorithm: str
    fastest_algorithm: str
    estimated: bool = Field(
        default=True,
        description=(
            "True when distances and times are straight-line estimates rather "
            "than measured on the road network."
        ),
    )
    routing_provider: str = Field(
        default="haversine", description="Where travel figures came from."
    )


class AlgorithmInfo(BaseModel):
    key: str
    label: str
    speed: str
    quality: str
    description: str
