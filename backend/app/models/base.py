"""Shared building blocks for Beanie documents."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Self

from beanie import (
    Document,
    Insert,
    PydanticObjectId,
    Replace,
    Save,
    SaveChanges,
    Update,
    before_event,
)
from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class GeoPoint(BaseModel):
    """A GeoJSON Point, the shape MongoDB's ``2dsphere`` index requires.

    This is the MongoDB counterpart of a PostGIS ``geography(Point, 4326)`` column.
    Note the coordinate order: GeoJSON is **[longitude, latitude]**, which is the
    reverse of how humans usually quote coordinates.
    """

    # A Literal rather than ``frozen=True``: frozen fields cannot be reassigned,
    # which breaks Beanie's validate-on-save model merge.
    type: Literal["Point"] = "Point"
    coordinates: list[float] = Field(min_length=2, max_length=2)

    @field_validator("coordinates")
    @classmethod
    def _validate_coordinates(cls, value: list[float]) -> list[float]:
        lng, lat = value
        if not -180.0 <= lng <= 180.0:
            raise ValueError(f"longitude {lng} is outside [-180, 180]")
        if not -90.0 <= lat <= 90.0:
            raise ValueError(f"latitude {lat} is outside [-90, 90]")
        return value

    @classmethod
    def from_lat_lng(cls, lat: float, lng: float) -> Self:
        return cls(coordinates=[lng, lat])

    @property
    def longitude(self) -> float:
        return self.coordinates[0]

    @property
    def latitude(self) -> float:
        return self.coordinates[1]

    def as_lat_lng(self) -> tuple[float, float]:
        return self.latitude, self.longitude


class TimestampedDocument(Document):
    """Base document carrying created/updated timestamps."""

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @before_event(Insert)
    def _set_created(self) -> None:
        now = utcnow()
        self.created_at = now
        self.updated_at = now

    # `Save` matters as much as the rest: Beanie wraps `save()` in its own SAVE
    # event, not Replace or Update, and `save()` is how this codebase persists
    # nearly everything. Without it listed here, `updated_at` stayed frozen at
    # whatever `_set_created` wrote, so every document claimed never to have been
    # touched since it was made.
    @before_event(Save, Replace, Update, SaveChanges)
    def _set_updated(self) -> None:
        self.updated_at = utcnow()

    @property
    def persisted_id(self) -> PydanticObjectId:
        """This document's id, for a document that must already be stored.

        Beanie types ``id`` as optional because a freshly constructed document has
        not been inserted yet. Anything loaded from the database — a dependency's
        current user, a query result — always has one, and callers that stringify
        the id need that guarantee: ``str(None)`` is the perfectly valid key
        ``"None"``, so a missing id would quietly match nothing instead of failing.
        """
        if self.id is None:  # pragma: no cover - unreachable for a loaded document
            raise RuntimeError(
                f"{type(self).__name__} has no id; it has not been inserted yet."
            )
        return self.id

    class Settings:
        use_state_management = True
        validate_on_save = True
