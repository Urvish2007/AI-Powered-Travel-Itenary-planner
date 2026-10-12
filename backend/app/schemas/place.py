"""Place request and response bodies."""

from __future__ import annotations

from datetime import datetime

from beanie import PydanticObjectId
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.base import GeoPoint
from app.models.enums import InterestTag, PlaceCategory
from app.models.place import OpeningHours, PlaceCost


class PlaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    category: PlaceCategory = PlaceCategory.OTHER
    tags: list[InterestTag] = Field(default_factory=list)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    address: str | None = Field(default=None, max_length=500)
    city: str = Field(min_length=1, max_length=200)
    country: str = Field(min_length=1, max_length=200)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    timezone: str = Field(default="UTC", max_length=64)
    rating: float = Field(default=0.0, ge=0, le=5)
    review_count: int = Field(default=0, ge=0)
    popularity: float = Field(default=0.5, ge=0, le=1)
    average_visit_minutes: int = Field(default=60, ge=5, le=1440)
    cost: PlaceCost = Field(default_factory=PlaceCost)
    opening_hours: list[OpeningHours] = Field(default_factory=list)
    wheelchair_accessible: bool | None = None
    family_friendly: bool = True
    image_url: str | None = None
    website: str | None = None

    def to_geo_point(self) -> GeoPoint:
        return GeoPoint.from_lat_lng(self.latitude, self.longitude)


class PlaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    category: PlaceCategory | None = None
    tags: list[InterestTag] | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    address: str | None = Field(default=None, max_length=500)
    rating: float | None = Field(default=None, ge=0, le=5)
    popularity: float | None = Field(default=None, ge=0, le=1)
    average_visit_minutes: int | None = Field(default=None, ge=5, le=1440)
    cost: PlaceCost | None = None
    opening_hours: list[OpeningHours] | None = None
    wheelchair_accessible: bool | None = None
    family_friendly: bool | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _coordinates_travel_together(self) -> PlaceUpdate:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be updated together")
        return self


class PlacePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: PydanticObjectId
    name: str
    slug: str
    description: str
    category: PlaceCategory
    tags: list[InterestTag]
    location: GeoPoint
    address: str | None = None
    city: str
    country: str
    timezone: str
    rating: float
    review_count: int
    popularity: float
    average_visit_minutes: int
    cost: PlaceCost
    opening_hours: list[OpeningHours]
    wheelchair_accessible: bool | None = None
    family_friendly: bool
    image_url: str | None = None
    website: str | None = None
    is_active: bool
    created_at: datetime

    # Present only on search responses.
    distance_km: float | None = None
    search_score: float | None = None


class NearbyQuery(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_km: float = Field(default=5.0, gt=0, le=200)
    category: PlaceCategory | None = None
    min_rating: float = Field(default=0.0, ge=0, le=5)
    limit: int = Field(default=20, ge=1, le=100)


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    city: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=20, ge=1, le=50)
