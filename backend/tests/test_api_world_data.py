"""Search endpoints exercised against the 2,000-place world corpus on a real DB.

``tests/test_world_seed.py`` validates the data files and the seeder through
mongomock, which does **not** implement ``$near``, the text index or the
aggregation used by the city summary. Those paths only run for real against a
real MongoDB, which is what these cover.

They skip automatically when no MongoDB is reachable (see ``conftest.py``).
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

PLACES = "/api/v1/places"

# City centres from the corpus's own metadata. Note the world data deliberately
# excludes Paris, Rome and Tokyo - the hand-written core seed covers those - so a
# world-only database has nothing near them.
KYOTO = (35.02107, 135.75385)
BARCELONA = (41.38879, 2.15899)
# Mid-Pacific: no seeded place is anywhere near it.
EMPTY_OCEAN = (-30.0, -140.0)


@pytest_asyncio.fixture
async def world(database: Any) -> int:
    """Seed the full world corpus into this test's throwaway database.

    Embeddings are generated because the semantic tests below need them; the
    hash embedder makes that cheap enough to do per test.
    """
    from app.db.world_seed import seed_world_places

    return await seed_world_places(embed=True)


class TestWorldSeed:
    async def test_seeds_the_whole_corpus(self, world: int) -> None:
        assert world == 2000

    async def test_is_idempotent(self, world: int) -> None:
        """Re-running the seeder must not duplicate places."""
        from app.db.world_seed import seed_world_places

        assert await seed_world_places(embed=False) == 0

    async def test_city_summary_lists_every_city(
        self, client: AsyncClient, world: int
    ) -> None:
        """The old aggregation capped at 100 and would silently drop cities."""
        response = await client.get(f"{PLACES}/cities")
        assert response.status_code == 200

        cities = response.json()
        assert len(cities) == 100
        assert len({city["city"] for city in cities}) == 100

    async def test_city_summary_centres_are_plausible(
        self, client: AsyncClient, world: int
    ) -> None:
        """A centre is the mean of the city's places, so it must be on Earth."""
        for city in (await client.get(f"{PLACES}/cities")).json():
            assert city["latitude"] is not None, city["city"]
            assert -90 <= city["latitude"] <= 90, city["city"]
            assert -180 <= city["longitude"] <= 180, city["city"]
            assert 0 < city["place_count"] <= 40, city["city"]


class TestGeospatialSearchOnWorldData:
    async def test_finds_places_near_a_world_city(
        self, client: AsyncClient, world: int
    ) -> None:
        latitude, longitude = KYOTO
        response = await client.get(
            f"{PLACES}/nearby",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "radius_km": 15,
                "limit": 20,
            },
        )
        assert response.status_code == 200

        results = response.json()
        assert results, "the 2dsphere index returned nothing in central Kyoto"
        assert all(place["distance_km"] <= 15.05 for place in results)

    async def test_results_are_distance_ordered(
        self, client: AsyncClient, world: int
    ) -> None:
        latitude, longitude = BARCELONA
        response = await client.get(
            f"{PLACES}/nearby",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "radius_km": 25,
                "limit": 20,
            },
        )
        distances = [place["distance_km"] for place in response.json()]
        assert distances == sorted(distances)

    async def test_empty_ocean_returns_nothing(
        self, client: AsyncClient, world: int
    ) -> None:
        latitude, longitude = EMPTY_OCEAN
        response = await client.get(
            f"{PLACES}/nearby",
            params={"latitude": latitude, "longitude": longitude, "radius_km": 50},
        )
        assert response.json() == []

    async def test_coordinates_are_stored_in_geojson_order(
        self, client: AsyncClient, world: int
    ) -> None:
        """[lng, lat] reversed would put every Kyoto pin in the wrong hemisphere."""
        latitude, longitude = KYOTO
        response = await client.get(
            f"{PLACES}/nearby",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "radius_km": 15,
                "limit": 5,
            },
        )
        for place in response.json():
            lng, lat = place["location"]["coordinates"]
            assert 30 < lat < 40, f"{place['name']} latitude {lat} is not in Japan"
            assert 135 < lng < 145, f"{place['name']} longitude {lng} is not in Japan"


class TestTextSearchOnWorldData:
    async def test_finds_a_place_by_name(self, client: AsyncClient, world: int) -> None:
        response = await client.get(
            f"{PLACES}/search", params={"q": "Sagrada", "city": "Barcelona"}
        )
        assert response.status_code == 200

        names = [place["name"] for place in response.json()]
        assert any("Sagrada" in name for name in names), names

    async def test_results_are_score_ordered(self, client: AsyncClient, world: int) -> None:
        response = await client.get(f"{PLACES}/search", params={"q": "museum", "limit": 20})
        scores = [place["search_score"] for place in response.json()]
        assert scores == sorted(scores, reverse=True)

    async def test_nonsense_matches_nothing(self, client: AsyncClient, world: int) -> None:
        response = await client.get(f"{PLACES}/search", params={"q": "zzqqxxnotathing"})
        assert response.json() == []


class TestSemanticSearchOnWorldData:
    async def test_shared_vocabulary_ranks_first(
        self, client: AsyncClient, world: int
    ) -> None:
        response = await client.post(
            f"{PLACES}/semantic-search",
            json={"query": "bamboo grove", "city": "Kyoto", "limit": 5},
        )
        assert response.status_code == 200

        results = response.json()
        assert results, "no semantic match for a phrase that appears in the data"
        assert "Bamboo" in results[0]["name"]

    async def test_scores_descend(self, client: AsyncClient, world: int) -> None:
        response = await client.post(
            f"{PLACES}/semantic-search",
            json={"query": "art museum paintings", "city": "Barcelona", "limit": 5},
        )
        scores = [place["search_score"] for place in response.json()]
        assert scores == sorted(scores, reverse=True)

    async def test_a_query_sharing_no_vocabulary_returns_nothing(
        self, client: AsyncClient, world: int
    ) -> None:
        """The hash embedder matches on shared words.

        With no overlap every score is zero and the ordering would be arbitrary,
        so the endpoint returns nothing rather than presenting three unrelated
        places as matches. A real embedding provider would score these properly.
        """
        response = await client.post(
            f"{PLACES}/semantic-search",
            json={"query": "zzqqxx nonsense tokens", "city": "Kyoto", "limit": 5},
        )
        assert response.status_code == 200
        assert response.json() == []

    async def test_city_filter_is_respected(self, client: AsyncClient, world: int) -> None:
        response = await client.post(
            f"{PLACES}/semantic-search",
            json={"query": "temple", "city": "Kyoto", "limit": 10},
        )
        assert {place["city"] for place in response.json()} <= {"Kyoto"}

    async def test_embeddings_are_never_returned(
        self, client: AsyncClient, world: int
    ) -> None:
        """Stored for search, but 384 floats per place has no business on the wire."""
        response = await client.post(
            f"{PLACES}/semantic-search", json={"query": "temple", "limit": 5}
        )
        assert "embedding" not in response.text
