"""Trip request and response bodies."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from beanie import PydanticObjectId
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import TripStatus
from app.models.trip import MAX_TRIP_DAYS, Destination, PartyComposition, TripConstraints


class DestinationInput(BaseModel):
    """A destination as a client supplies it, in human coordinate order."""

    city: str = Field(min_length=1, max_length=200)
    country: str = Field(min_length=1, max_length=200)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone: str = Field(default="UTC", max_length=64)

    def to_destination(self) -> Destination:
        from app.models.base import GeoPoint

        return Destination(
            city=self.city,
            country=self.country,
            location=GeoPoint.from_lat_lng(self.latitude, self.longitude),
            timezone=self.timezone,
        )


class TripCreate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    destination: DestinationInput
    start_date: date
    end_date: date
    party: PartyComposition = Field(default_factory=PartyComposition)
    constraints: TripConstraints = Field(default_factory=TripConstraints)

    @model_validator(mode="after")
    def _check_dates(self) -> TripCreate:
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        if (self.end_date - self.start_date).days + 1 > MAX_TRIP_DAYS:
            raise ValueError(f"a trip may span at most {MAX_TRIP_DAYS} days")
        return self


class TripUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    destination: DestinationInput | None = None
    start_date: date | None = None
    end_date: date | None = None
    party: PartyComposition | None = None
    constraints: TripConstraints | None = None
    status: TripStatus | None = None
    is_public: bool | None = None


class TripPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: PydanticObjectId
    user_id: PydanticObjectId
    title: str
    destination: Destination
    start_date: date
    end_date: date
    duration_days: int
    party: PartyComposition
    constraints: TripConstraints
    status: TripStatus
    active_itinerary_id: PydanticObjectId | None = None
    is_public: bool
    share_token: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskAccepted(BaseModel):
    """Handle for a planning run queued on a worker."""

    task_id: str
    trip_id: str
    status: str = "queued"
    poll_url: str


class TaskStatus(BaseModel):
    task_id: str
    state: str = Field(
        description="Celery task state: PENDING, STARTED, SUCCESS, FAILURE or RETRY."
    )
    ready: bool = False
    result: dict[str, Any] | None = None
    error: str | None = None


class GenerateItineraryRequest(BaseModel):
    """Kick off a planning run.

    ``brief`` is the free-text field the LLM interprets; everything else is
    already structured on the trip.
    """

    brief: str = Field(
        default="",
        max_length=4000,
        description="Optional free-text description of what the traveller wants.",
    )
    regenerate: bool = Field(
        default=False,
        description="Create a new version even when the trip already has an itinerary.",
    )
