"""A licensed photograph of a city, with what its licence requires us to show.

One document per city, written whether or not a usable photo was found: a
``none`` record is what lets the fetcher resume without asking Wikimedia about
the same city again.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field
from pymongo import ASCENDING, IndexModel

from app.models.base import TimestampedDocument


class PhotoStatus(StrEnum):
    FOUND = "found"
    # Looked, and found nothing we may use (no image, a non-free licence, or a
    # page that turned out to be about somewhere else).
    NONE = "none"


class CityPhoto(TimestampedDocument):
    city: str
    country: str
    status: PhotoStatus
    # Why there is no photo, for the fetch report. Never shown to travellers.
    reason: str | None = None

    image_url: str | None = None
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)

    # Attribution: title (the file page), author, source and licence.
    author: str | None = None
    license: str | None = None
    license_url: str | None = None
    source_url: str | None = None
    source: str = "wikimedia"

    class Settings(TimestampedDocument.Settings):
        name = "city_photos"
        indexes = [
            IndexModel(
                [("city", ASCENDING), ("country", ASCENDING)],
                name="city_country_unique",
                unique=True,
            )
        ]
