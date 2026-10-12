"""Tests for index behaviour.

Indexes are declared on the documents and created by Beanie at startup, which
means a wrong declaration fails at *runtime*, on real user traffic, rather than
at import. These tests assert the behaviour each index is supposed to produce.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError

from app.models.base import GeoPoint
from app.models.place import Place
from app.models.trip import Destination, Trip
from app.models.user import User

pytestmark = pytest.mark.asyncio


def _trip(user_id: PydanticObjectId, title: str) -> Trip:
    start = date.today() + timedelta(days=10)
    return Trip(
        user_id=user_id,
        title=title,
        destination=Destination(
            city="Paris",
            country="France",
            location=GeoPoint.from_lat_lng(48.8566, 2.3522),
        ),
        start_date=start,
        end_date=start + timedelta(days=2),
    )


class TestShareTokenIndex:
    async def test_many_unshared_trips_can_coexist(self, database: Any) -> None:
        """The regression that a sparse unique index would cause.

        Every unshared trip stores ``share_token: null``. Under a sparse unique
        index the second such trip is rejected with a duplicate key error, so a
        user could never create more than one trip.
        """
        user_id = PydanticObjectId()
        for i in range(5):
            await _trip(user_id, f"Trip {i}").insert()

        assert await Trip.find(Trip.user_id == user_id).count() == 5

    async def test_share_tokens_are_still_unique(self, database: Any) -> None:
        user_id = PydanticObjectId()

        first = _trip(user_id, "First")
        first.share_token = "a-specific-token"
        await first.insert()

        second = _trip(user_id, "Second")
        second.share_token = "a-specific-token"

        with pytest.raises(DuplicateKeyError):
            await second.insert()


class TestUniqueIndexes:
    async def test_email_is_unique(self, database: Any) -> None:
        from app.core.security import hash_password

        await User(
            email="dupe@example.com",
            full_name="First",
            hashed_password=hash_password("password123"),
        ).insert()

        with pytest.raises(DuplicateKeyError):
            await User(
                email="dupe@example.com",
                full_name="Second",
                hashed_password=hash_password("password123"),
            ).insert()

    async def test_place_slug_is_unique(self, database: Any) -> None:
        def place() -> Place:
            return Place(
                name="Somewhere",
                slug="somewhere-paris",
                location=GeoPoint.from_lat_lng(48.86, 2.34),
                city="Paris",
                country="France",
            )

        await place().insert()
        with pytest.raises(DuplicateKeyError):
            await place().insert()


class TestGeospatialIndex:
    async def test_near_query_returns_ordered_results(self, database: Any) -> None:
        """Proves the 2dsphere index exists and coordinates are in GeoJSON order."""
        anchor = (48.8584, 2.2945)  # Eiffel Tower
        for i, (name, lat, lng) in enumerate(
            [
                ("Close", 48.8590, 2.2950),
                ("Medium", 48.8606, 2.3376),
                ("Far", 48.8867, 2.3431),
            ]
        ):
            await Place(
                name=name,
                slug=f"geo-{i}",
                location=GeoPoint.from_lat_lng(lat, lng),
                city="Paris",
                country="France",
            ).insert()

        results = await Place.find(
            {
                "location": {
                    "$nearSphere": {
                        "$geometry": {
                            "type": "Point",
                            "coordinates": [anchor[1], anchor[0]],
                        },
                        "$maxDistance": 10_000,
                    }
                }
            }
        ).to_list()

        assert [p.name for p in results] == ["Close", "Medium", "Far"]

    async def test_max_distance_excludes_far_places(self, database: Any) -> None:
        await Place(
            name="Tokyo place",
            slug="tokyo-place",
            location=GeoPoint.from_lat_lng(35.68, 139.76),
            city="Tokyo",
            country="Japan",
        ).insert()

        results = await Place.find(
            {
                "location": {
                    "$nearSphere": {
                        "$geometry": {"type": "Point", "coordinates": [2.2945, 48.8584]},
                        "$maxDistance": 50_000,
                    }
                }
            }
        ).to_list()
        assert results == []


class TestTextIndex:
    async def test_text_search_weights_name_above_description(self, database: Any) -> None:
        await Place(
            name="Cathedral",
            slug="cathedral",
            description="A building.",
            location=GeoPoint.from_lat_lng(48.85, 2.35),
            city="Paris",
            country="France",
        ).insert()
        await Place(
            name="Corner Shop",
            slug="corner-shop",
            description="Next to the cathedral, sells postcards.",
            location=GeoPoint.from_lat_lng(48.85, 2.35),
            city="Paris",
            country="France",
        ).insert()

        from app.db.mongodb import get_database

        documents = await (
            get_database()["places"]
            .find({"$text": {"$search": "cathedral"}}, {"score": {"$meta": "textScore"}})
            .sort([("score", {"$meta": "textScore"})])
            .to_list(length=10)
        )
        assert next(d["name"] for d in documents) == "Cathedral"
