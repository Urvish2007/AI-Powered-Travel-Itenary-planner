"""Integration tests for place discovery: filtering, geo, text and vector search."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

PLACES = "/api/v1/places"

# The Eiffel Tower, used as the anchor for radius queries.
EIFFEL_LAT, EIFFEL_LNG = 48.8584, 2.2945


class TestListing:
    async def test_lists_seeded_places(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        response = await client.get(PLACES, params={"limit": 5})
        assert response.status_code == 200

        body = response.json()
        assert body["meta"]["total"] > 0
        assert len(body["items"]) <= 5

    async def test_filters_by_city(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        response = await client.get(PLACES, params={"city": "Paris", "limit": 50})
        assert response.status_code == 200
        assert {p["city"] for p in response.json()["items"]} == {"Paris"}

    async def test_filters_by_category(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        response = await client.get(PLACES, params={"category": "museum", "limit": 50})
        assert response.status_code == 200
        assert {p["category"] for p in response.json()["items"]} == {"museum"}

    async def test_filters_by_minimum_rating(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        response = await client.get(PLACES, params={"min_rating": 4.6, "limit": 50})
        assert response.status_code == 200
        assert all(p["rating"] >= 4.6 for p in response.json()["items"])

    async def test_pagination_does_not_repeat_items(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        first = await client.get(PLACES, params={"limit": 5, "skip": 0})
        second = await client.get(PLACES, params={"limit": 5, "skip": 5})

        ids_a = {p["id"] for p in first.json()["items"]}
        ids_b = {p["id"] for p in second.json()["items"]}
        assert not ids_a & ids_b

    async def test_rejects_an_over_large_limit(self, client: AsyncClient) -> None:
        assert (await client.get(PLACES, params={"limit": 500})).status_code == 422

    async def test_never_exposes_embeddings(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        """Embeddings are stored but must not be part of the public payload."""
        response = await client.get(PLACES, params={"limit": 5})
        assert "embedding" not in response.text


class TestGeospatialSearch:
    async def test_finds_places_within_the_radius(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        response = await client.get(
            f"{PLACES}/nearby",
            params={
                "latitude": EIFFEL_LAT,
                "longitude": EIFFEL_LNG,
                "radius_km": 3,
                "limit": 20,
            },
        )
        assert response.status_code == 200

        results = response.json()
        assert results, "the 2dsphere query returned nothing near the Eiffel Tower"
        assert all(p["distance_km"] <= 3.05 for p in results)

    async def test_results_are_ordered_by_distance(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        response = await client.get(
            f"{PLACES}/nearby",
            params={
                "latitude": EIFFEL_LAT,
                "longitude": EIFFEL_LNG,
                "radius_km": 10,
                "limit": 20,
            },
        )
        distances = [p["distance_km"] for p in response.json()]
        assert distances == sorted(distances)

    async def test_a_tiny_radius_excludes_distant_places(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        wide = await client.get(
            f"{PLACES}/nearby",
            params={"latitude": EIFFEL_LAT, "longitude": EIFFEL_LNG, "radius_km": 10},
        )
        narrow = await client.get(
            f"{PLACES}/nearby",
            params={"latitude": EIFFEL_LAT, "longitude": EIFFEL_LNG, "radius_km": 0.5},
        )
        assert len(narrow.json()) < len(wide.json())

    async def test_another_continent_returns_nothing(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        response = await client.get(
            f"{PLACES}/nearby",
            params={"latitude": -33.8688, "longitude": 151.2093, "radius_km": 5},
        )
        assert response.json() == []

    @pytest.mark.parametrize(
        ("latitude", "longitude"), [(91.0, 0.0), (0.0, 181.0), (-91.0, 0.0)]
    )
    async def test_rejects_impossible_coordinates(
        self, client: AsyncClient, latitude: float, longitude: float
    ) -> None:
        response = await client.get(
            f"{PLACES}/nearby", params={"latitude": latitude, "longitude": longitude}
        )
        assert response.status_code == 422


class TestTextSearch:
    async def test_finds_matches_by_keyword(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        response = await client.get(f"{PLACES}/search", params={"q": "museum"})
        assert response.status_code == 200

        results = response.json()
        assert results
        assert all(p["search_score"] is not None for p in results)

    async def test_results_are_score_ordered(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        response = await client.get(f"{PLACES}/search", params={"q": "museum art"})
        scores = [p["search_score"] for p in response.json()]
        assert scores == sorted(scores, reverse=True)

    async def test_nonsense_query_returns_nothing(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        response = await client.get(f"{PLACES}/search", params={"q": "zzzqqqxxnotathing"})
        assert response.json() == []

    async def test_rejects_a_too_short_query(self, client: AsyncClient) -> None:
        assert (await client.get(f"{PLACES}/search", params={"q": "a"})).status_code == 422


class TestSemanticSearch:
    async def test_returns_ranked_results(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        response = await client.post(
            f"{PLACES}/semantic-search",
            json={"query": "impressionist paintings and sculpture", "limit": 5},
        )
        assert response.status_code == 200

        results = response.json()
        assert results, "vector search returned nothing; are embeddings persisted?"
        scores = [p["search_score"] for p in results]
        assert scores == sorted(scores, reverse=True)

    async def test_can_be_scoped_to_a_city(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        response = await client.post(
            f"{PLACES}/semantic-search",
            json={"query": "temple shrine", "city": "Tokyo", "limit": 5},
        )
        assert response.status_code == 200
        assert {p["city"] for p in response.json()} <= {"Tokyo"}

    async def test_matches_meaning_rather_than_exact_words(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        """A query sharing vocabulary with gardens should surface green space."""
        response = await client.post(
            f"{PLACES}/semantic-search",
            json={"query": "gardens park green lawn trees", "city": "Paris", "limit": 5},
        )
        names = " ".join(p["name"].lower() for p in response.json())
        assert "garden" in names or "park" in names or "canal" in names


class TestPlaceDetail:
    async def test_fetches_one_place(
        self, client: AsyncClient, seeded: dict[str, int]
    ) -> None:
        listing = await client.get(PLACES, params={"limit": 1})
        place_id = listing.json()["items"][0]["id"]

        response = await client.get(f"{PLACES}/{place_id}")
        assert response.status_code == 200
        assert response.json()["id"] == place_id

    async def test_missing_place_is_404(self, client: AsyncClient) -> None:
        response = await client.get(f"{PLACES}/000000000000000000000000")
        assert response.status_code == 404

    async def test_malformed_id_is_404_not_500(self, client: AsyncClient) -> None:
        response = await client.get(f"{PLACES}/definitely-not-an-objectid")
        assert response.status_code == 404

    async def test_city_summary(self, client: AsyncClient, seeded: dict[str, int]) -> None:
        response = await client.get(f"{PLACES}/cities")
        assert response.status_code == 200

        cities = {row["city"] for row in response.json()}
        assert {"Paris", "Rome", "Tokyo"} <= cities


class TestPlaceAdministration:
    def _payload(self, **overrides: Any) -> dict[str, Any]:
        payload = {
            "name": "Test Museum",
            "description": "A museum created by the test suite.",
            "category": "museum",
            "tags": ["art"],
            "latitude": 48.86,
            "longitude": 2.34,
            "city": "Paris",
            "country": "France",
            "average_visit_minutes": 90,
            "rating": 4.2,
            "cost": {"amount": 12.0, "currency": "EUR"},
        }
        payload.update(overrides)
        return payload

    async def test_ordinary_users_cannot_create(
        self, client: AsyncClient, user_token: dict[str, str]
    ) -> None:
        response = await client.post(PLACES, headers=user_token, json=self._payload())
        assert response.status_code == 403

    async def test_admin_can_create(
        self, client: AsyncClient, admin_token: dict[str, str]
    ) -> None:
        response = await client.post(PLACES, headers=admin_token, json=self._payload())
        assert response.status_code == 201

        body = response.json()
        assert body["slug"]
        assert body["location"]["coordinates"] == [2.34, 48.86]

    async def test_duplicate_names_get_distinct_slugs(
        self, client: AsyncClient, admin_token: dict[str, str]
    ) -> None:
        first = await client.post(PLACES, headers=admin_token, json=self._payload())
        second = await client.post(PLACES, headers=admin_token, json=self._payload())

        assert second.status_code == 201
        assert first.json()["slug"] != second.json()["slug"]

    async def test_update_applies_changes(
        self, client: AsyncClient, admin_token: dict[str, str]
    ) -> None:
        created = await client.post(PLACES, headers=admin_token, json=self._payload())
        place_id = created.json()["id"]

        response = await client.patch(
            f"{PLACES}/{place_id}", headers=admin_token, json={"rating": 4.9}
        )
        assert response.status_code == 200
        assert response.json()["rating"] == 4.9

    async def test_coordinates_must_be_updated_together(
        self, client: AsyncClient, admin_token: dict[str, str]
    ) -> None:
        """Half a coordinate pair would silently move the place onto a meridian."""
        created = await client.post(PLACES, headers=admin_token, json=self._payload())
        place_id = created.json()["id"]

        response = await client.patch(
            f"{PLACES}/{place_id}", headers=admin_token, json={"latitude": 48.9}
        )
        assert response.status_code == 422

    async def test_soft_delete_hides_the_place_from_listings(
        self, client: AsyncClient, admin_token: dict[str, str]
    ) -> None:
        created = await client.post(PLACES, headers=admin_token, json=self._payload())
        place_id = created.json()["id"]

        assert (
            await client.delete(f"{PLACES}/{place_id}", headers=admin_token)
        ).status_code == 200

        listing = await client.get(PLACES, params={"limit": 100, "city": "Paris"})
        assert place_id not in {p["id"] for p in listing.json()["items"]}

        # The document itself survives, so existing itineraries keep resolving.
        assert (await client.get(f"{PLACES}/{place_id}")).status_code == 200

    async def test_hard_delete_removes_the_document(
        self, client: AsyncClient, admin_token: dict[str, str]
    ) -> None:
        created = await client.post(PLACES, headers=admin_token, json=self._payload())
        place_id = created.json()["id"]

        assert (
            await client.delete(
                f"{PLACES}/{place_id}", headers=admin_token, params={"hard": "true"}
            )
        ).status_code == 200
        assert (await client.get(f"{PLACES}/{place_id}")).status_code == 404

    async def test_created_places_are_semantically_searchable(
        self, client: AsyncClient, admin_token: dict[str, str]
    ) -> None:
        await client.post(
            PLACES,
            headers=admin_token,
            json=self._payload(
                name="Observatory of Rare Astronomy",
                description="Telescopes, planetarium shows and astronomy exhibits.",
            ),
        )
        response = await client.post(
            f"{PLACES}/semantic-search",
            json={"query": "telescopes planetarium astronomy", "limit": 5},
        )
        assert "Observatory of Rare Astronomy" in {p["name"] for p in response.json()}
