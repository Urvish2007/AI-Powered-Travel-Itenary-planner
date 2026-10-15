"""Trip documents: the user's request for a plan."""

from __future__ import annotations

from datetime import date

import pymongo
from beanie import PydanticObjectId
from pydantic import BaseModel, Field, model_validator
from pymongo import IndexModel

from app.core.config import OptimizerAlgorithm
from app.models.base import GeoPoint, TimestampedDocument
from app.models.enums import (
    AccessibilityNeed,
    BudgetLevel,
    InterestTag,
    TransportMode,
    TravelPace,
    TripStatus,
)

MAX_TRIP_DAYS = 30


class Destination(BaseModel):
    city: str = Field(min_length=1, max_length=200)
    country: str = Field(min_length=1, max_length=200)
    location: GeoPoint
    timezone: str = Field(default="UTC", max_length=64)


class PartyComposition(BaseModel):
    adults: int = Field(default=1, ge=1, le=20)
    children: int = Field(default=0, ge=0, le=20)
    infants: int = Field(default=0, ge=0, le=10)

    @property
    def total(self) -> int:
        return self.adults + self.children + self.infants

    @property
    def has_children(self) -> bool:
        return self.children > 0 or self.infants > 0


class TripConstraints(BaseModel):
    """Hard and soft limits the optimiser must respect."""

    pace: TravelPace = TravelPace.MODERATE
    budget_level: BudgetLevel = BudgetLevel.MODERATE
    budget_total: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    interests: list[InterestTag] = Field(default_factory=list)
    preferred_transport: TransportMode = TransportMode.WALKING
    accessibility_needs: list[AccessibilityNeed] = Field(default_factory=list)
    dietary_restrictions: list[str] = Field(default_factory=list)
    day_start_time: str = Field(default="09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    day_end_time: str = Field(default="21:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    # Which route algorithm plans this trip. Optional on purpose: ``None`` means
    # "not chosen", which is how every trip stored before this field existed
    # reads, and defers to OPTIMIZER_ALGORITHM. See `engine.resolve_algorithm`
    # for the full precedence order.
    optimizer_algorithm: OptimizerAlgorithm | None = None
    max_daily_walking_km: float = Field(default=8.0, ge=0, le=100)
    # Places the user insists on; the optimiser treats these as mandatory nodes.
    must_include_place_ids: list[PydanticObjectId] = Field(default_factory=list)
    must_avoid_place_ids: list[PydanticObjectId] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check_day_window(self) -> TripConstraints:
        if self.day_start_time >= self.day_end_time:
            raise ValueError("day_start_time must be earlier than day_end_time")
        return self


class Trip(TimestampedDocument):
    user_id: PydanticObjectId
    title: str = Field(min_length=1, max_length=300)
    destination: Destination
    start_date: date
    end_date: date
    party: PartyComposition = Field(default_factory=PartyComposition)
    constraints: TripConstraints = Field(default_factory=TripConstraints)
    status: TripStatus = TripStatus.DRAFT
    # The itinerary currently shown to the user; older versions stay queryable.
    active_itinerary_id: PydanticObjectId | None = None
    is_public: bool = False
    share_token: str | None = None

    class Settings(TimestampedDocument.Settings):
        name = "trips"
        indexes = [
            IndexModel(
                [("user_id", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)],
                name="trip_user_recent",
            ),
            IndexModel([("status", pymongo.ASCENDING)], name="trip_status"),
            # A *partial* index, not a sparse one. Sparse only skips documents
            # where the field is absent; Beanie always writes the field, so an
            # unshared trip stores ``share_token: null`` explicitly and a sparse
            # unique index would reject every trip after the first. The partial
            # filter indexes only real tokens.
            IndexModel(
                [("share_token", pymongo.ASCENDING)],
                unique=True,
                partialFilterExpression={"share_token": {"$type": "string"}},
                name="uniq_share_token",
            ),
            IndexModel(
                [("destination.city", pymongo.ASCENDING)], name="trip_destination_city"
            ),
        ]

    @model_validator(mode="after")
    def _check_dates(self) -> Trip:
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        if self.duration_days > MAX_TRIP_DAYS:
            raise ValueError(f"a trip may span at most {MAX_TRIP_DAYS} days")
        return self

    @property
    def duration_days(self) -> int:
        """Inclusive day count: a same-day trip is 1 day, not 0."""
        return (self.end_date - self.start_date).days + 1
