"""Itinerary documents: the generated, optimised plan."""

from __future__ import annotations

from datetime import date

import pymongo
from beanie import PydanticObjectId
from pydantic import BaseModel, Field, computed_field, model_validator
from pymongo import IndexModel

from app.models.base import GeoPoint, TimestampedDocument
from app.models.enums import ActivityKind, ItineraryStatus, TransportMode


class TravelLeg(BaseModel):
    """Movement between two consecutive activities."""

    mode: TransportMode = TransportMode.WALKING
    distance_km: float = Field(default=0.0, ge=0)
    duration_minutes: int = Field(default=0, ge=0)
    # Optional encoded polyline, so the frontend can draw the leg on a map.
    polyline: str | None = None


class Activity(BaseModel):
    """One scheduled block within a day."""

    place_id: PydanticObjectId | None = None
    title: str = Field(min_length=1, max_length=300)
    kind: ActivityKind = ActivityKind.VISIT
    description: str = Field(default="", max_length=2000)
    # Local clock at the destination, "HH:MM".
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    duration_minutes: int = Field(ge=0, le=1440)
    location: GeoPoint | None = None
    address: str | None = Field(default=None, max_length=500)
    estimated_cost: float = Field(default=0.0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    # Why this activity is here, so the plan can explain itself to the user.
    rationale: str = Field(default="", max_length=1000)
    booking_required: bool = False
    booking_url: str | None = None
    travel_from_previous: TravelLeg | None = None
    order: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check_times(self) -> Activity:
        if self.end_time < self.start_time:
            raise ValueError(
                f"activity {self.title!r} ends ({self.end_time}) before it starts "
                f"({self.start_time})"
            )
        return self


class ItineraryDay(BaseModel):
    day_number: int = Field(ge=1)
    day_date: date
    title: str = Field(default="", max_length=300)
    summary: str = Field(default="", max_length=2000)
    activities: list[Activity] = Field(default_factory=list)
    total_distance_km: float = Field(default=0.0, ge=0)
    total_walking_km: float = Field(default=0.0, ge=0)
    estimated_cost: float = Field(default=0.0, ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def activity_count(self) -> int:
        return sum(1 for a in self.activities if a.kind is not ActivityKind.TRAVEL)


class OptimizationReport(BaseModel):
    """What the OR-Tools pass achieved, surfaced for transparency."""

    ran: bool = False
    solver_status: str = "not_run"
    objective_value: float | None = None
    duration_seconds: float = 0.0
    distance_before_km: float | None = None
    distance_after_km: float | None = None
    places_scheduled: int = 0
    places_dropped: int = 0
    dropped_reasons: dict[str, str] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def distance_saved_km(self) -> float | None:
        if self.distance_before_km is None or self.distance_after_km is None:
            return None
        return round(self.distance_before_km - self.distance_after_km, 3)


class GenerationMeta(BaseModel):
    """Provenance for the AI pass that produced this itinerary."""

    provider: str = "stub"
    model: str = "stub"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    generated_by: str = "pipeline"
    warnings: list[str] = Field(default_factory=list)


class Itinerary(TimestampedDocument):
    trip_id: PydanticObjectId
    user_id: PydanticObjectId
    version: int = Field(default=1, ge=1)
    status: ItineraryStatus = ItineraryStatus.PENDING
    title: str = Field(default="", max_length=300)
    summary: str = Field(default="", max_length=4000)
    days: list[ItineraryDay] = Field(default_factory=list)
    total_estimated_cost: float = Field(default=0.0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    total_distance_km: float = Field(default=0.0, ge=0)
    optimization: OptimizationReport = Field(default_factory=OptimizationReport)
    generation: GenerationMeta = Field(default_factory=GenerationMeta)
    error_message: str | None = None

    class Settings(TimestampedDocument.Settings):
        name = "itineraries"
        indexes = [
            IndexModel(
                [("trip_id", pymongo.ASCENDING), ("version", pymongo.DESCENDING)],
                name="itinerary_trip_version",
            ),
            IndexModel(
                [("user_id", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)],
                name="itinerary_user_recent",
            ),
            IndexModel([("status", pymongo.ASCENDING)], name="itinerary_status"),
        ]

    def recalculate_totals(self) -> None:
        """Roll per-activity figures up into day and itinerary totals."""
        for day in self.days:
            day.estimated_cost = round(sum(a.estimated_cost for a in day.activities), 2)
            day.total_distance_km = round(
                sum(
                    a.travel_from_previous.distance_km
                    for a in day.activities
                    if a.travel_from_previous
                ),
                3,
            )
            day.total_walking_km = round(
                sum(
                    a.travel_from_previous.distance_km
                    for a in day.activities
                    if a.travel_from_previous
                    and a.travel_from_previous.mode is TransportMode.WALKING
                ),
                3,
            )
        self.total_estimated_cost = round(sum(d.estimated_cost for d in self.days), 2)
        self.total_distance_km = round(sum(d.total_distance_km for d in self.days), 3)
