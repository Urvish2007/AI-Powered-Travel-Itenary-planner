"""Point-of-interest documents: geospatial, searchable and embeddable."""

from __future__ import annotations

import pymongo
from pydantic import BaseModel, Field, model_validator
from pymongo import IndexModel

from app.models.base import GeoPoint, TimestampedDocument
from app.models.enums import InterestTag, PlaceCategory


class OpeningHours(BaseModel):
    """Opening window for one weekday.

    ``day_of_week`` follows Python's convention: Monday is 0, Sunday is 6.
    A place that is closed all day carries ``closed=True`` and no times.
    """

    day_of_week: int = Field(ge=0, le=6)
    opens: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    closes: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    closed: bool = False

    @model_validator(mode="after")
    def _check_window(self) -> OpeningHours:
        if not self.closed and (self.opens is None or self.closes is None):
            raise ValueError("an open day requires both 'opens' and 'closes'")
        return self


class PlaceCost(BaseModel):
    """Typical cost of admission, per adult."""

    amount: float = Field(default=0.0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    is_free: bool = False

    @model_validator(mode="after")
    def _sync_free(self) -> PlaceCost:
        if self.amount == 0:
            self.is_free = True
        return self


class Place(TimestampedDocument):
    """A visitable place. The planner's atomic unit of "something to do"."""

    name: str = Field(min_length=1, max_length=300)
    slug: str = Field(min_length=1, max_length=320)
    description: str = Field(default="", max_length=4000)
    category: PlaceCategory = PlaceCategory.OTHER
    tags: list[InterestTag] = Field(default_factory=list)

    # ---- Location (MongoDB 2dsphere; the PostGIS replacement) ----
    location: GeoPoint
    address: str | None = Field(default=None, max_length=500)
    city: str = Field(min_length=1, max_length=200)
    country: str = Field(min_length=1, max_length=200)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    timezone: str = Field(default="UTC", max_length=64)

    # ---- Planning signals ----
    rating: float = Field(default=0.0, ge=0, le=5)
    review_count: int = Field(default=0, ge=0)
    popularity: float = Field(
        default=0.5, ge=0, le=1, description="Normalised 0-1 crowd-draw score."
    )
    # How long a typical visit lasts; drives the optimiser's service times.
    average_visit_minutes: int = Field(default=60, ge=5, le=1440)
    cost: PlaceCost = Field(default_factory=PlaceCost)
    opening_hours: list[OpeningHours] = Field(default_factory=list)

    # ---- Accessibility ----
    wheelchair_accessible: bool | None = None
    family_friendly: bool = True

    # ---- Media & provenance ----
    image_url: str | None = None
    website: str | None = None
    source: str = Field(default="seed", max_length=64)
    external_id: str | None = None

    # ---- Vector search (the pgvector replacement) ----
    # Note: no ``exclude=True`` here. Beanie serialises the document through the
    # same model dump it uses to persist it, so excluding these fields would
    # write NULL to MongoDB and silently disable semantic search. They are kept
    # out of API responses by the response schemas instead (see PlacePublic),
    # which is the right place for that decision anyway.
    embedding: list[float] | None = Field(
        default=None,
        description="Dense embedding of name+description+tags, used for semantic recall.",
    )
    embedding_model: str | None = None

    is_active: bool = True

    class Settings(TimestampedDocument.Settings):
        name = "places"
        indexes = [
            # Geospatial: powers $near / $geoWithin radius search.
            IndexModel([("location", pymongo.GEOSPHERE)], name="place_location_2dsphere"),
            # Full-text: powers keyword search across name/description/city.
            IndexModel(
                [
                    ("name", pymongo.TEXT),
                    ("description", pymongo.TEXT),
                    ("city", pymongo.TEXT),
                ],
                name="place_text",
                weights={"name": 10, "city": 5, "description": 1},
            ),
            IndexModel(
                [("city", pymongo.ASCENDING), ("category", pymongo.ASCENDING)],
                name="place_city_category",
            ),
            IndexModel([("slug", pymongo.ASCENDING)], unique=True, name="uniq_place_slug"),
            IndexModel(
                [("city", pymongo.ASCENDING), ("rating", pymongo.DESCENDING)],
                name="place_city_rating",
            ),
            IndexModel([("tags", pymongo.ASCENDING)], name="place_tags"),
        ]

    def is_open_on(self, day_of_week: int) -> bool:
        """True when the place admits visitors on ``day_of_week``.

        A place with no recorded hours is treated as always open, which keeps
        sparsely-sourced data from being silently excluded from every plan.
        """
        if not self.opening_hours:
            return True
        for window in self.opening_hours:
            if window.day_of_week == day_of_week:
                return not window.closed
        return False

    def window_on(self, day_of_week: int) -> tuple[str, str] | None:
        """Return ``(opens, closes)`` for the day, or ``None`` when unconstrained."""
        for window in self.opening_hours:
            if (
                window.day_of_week == day_of_week
                and not window.closed
                and window.opens
                and window.closes
            ):
                return window.opens, window.closes
        return None
