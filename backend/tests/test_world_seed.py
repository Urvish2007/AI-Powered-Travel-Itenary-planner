"""The worldwide places corpus: data integrity and the seeding path.

The data tests need nothing but the files. The seeding tests run Beanie against
``mongomock-motor`` (an in-memory stand-in for Motor), so they verify the batch
insert and idempotency logic without a MongoDB server. They deliberately do not
cover geo or vector queries, which a mock cannot answer; those run in the
real-database suite.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from app.db import world_seed
from app.db.world_seed import (
    ALLOWED_FAR,
    CITIES_FILE,
    WorldDataError,
    load_cities,
    parse_world_places,
)
from app.models.enums import PlaceCategory

EXPECTED_CITIES = 100
PLACES_PER_CITY = 20
EXPECTED_PLACES = EXPECTED_CITIES * PLACES_PER_CITY
# Beyond this a place is far more likely a typo than a real day trip.
MAX_KM_FROM_CENTRE = 65.0


def _km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return parse_world_places()


class TestCorpusShape:
    def test_has_exactly_2000_places_in_100_cities(self, rows: list[dict]) -> None:
        assert len(rows) == EXPECTED_PLACES
        assert len({r["city"] for r in rows}) == EXPECTED_CITIES

    def test_every_city_has_twenty_places(self, rows: list[dict]) -> None:
        counts = Counter(r["city"] for r in rows)
        assert {c: n for c, n in counts.items() if n != PLACES_PER_CITY} == {}

    def test_cities_json_matches_the_data_files(self, rows: list[dict]) -> None:
        assert set(load_cities()) == {r["city"] for r in rows}

    def test_every_city_has_meal_places_for_the_planner(self, rows: list[dict]) -> None:
        """The planner slots lunch and dinner from restaurants and cafés."""
        meals: dict[str, int] = defaultdict(int)
        for r in rows:
            if r["category"] in (PlaceCategory.RESTAURANT, PlaceCategory.CAFE):
                meals[r["city"]] += 1
        assert {c: meals[c] for c in load_cities() if meals[c] < 2} == {}

    def test_no_city_is_dominated_by_food(self, rows: list[dict]) -> None:
        meals: dict[str, int] = defaultdict(int)
        for r in rows:
            if r["category"] in (PlaceCategory.RESTAURANT, PlaceCategory.CAFE):
                meals[r["city"]] += 1
        assert max(meals.values()) <= 8


class TestCorpusQuality:
    def test_places_are_near_their_city(self, rows: list[dict]) -> None:
        cities = load_cities()
        far = []
        for r in rows:
            c = cities[r["city"]]
            km = _km(c.latitude, c.longitude, r["lat"], r["lng"])
            if km > MAX_KM_FROM_CENTRE and (r["city"], r["name"]) not in ALLOWED_FAR:
                far.append((r["city"], r["name"], round(km, 1)))
        assert far == [], "likely coordinate typos (or add to ALLOWED_FAR)"

    def test_no_two_places_in_a_city_share_exact_coordinates(
        self, rows: list[dict]
    ) -> None:
        """Identical coordinates stack into one map pin and hide a place."""
        seen: dict[tuple, str] = {}
        clashes = []
        for r in rows:
            key = (r["city"], r["lat"], r["lng"])
            if key in seen:
                clashes.append((r["city"], seen[key], r["name"]))
            seen[key] = r["name"]
        assert clashes == []

    def test_names_are_unique_within_a_city(self, rows: list[dict]) -> None:
        names = Counter((r["city"], r["name"].casefold()) for r in rows)
        assert [k for k, n in names.items() if n > 1] == []

    def test_derived_fields_are_in_valid_ranges(self, rows: list[dict]) -> None:
        for r in rows:
            assert 0.0 <= r["rating"] <= 5.0
            assert 0.0 < r["popularity"] <= 1.0
            assert 10 <= r["minutes"] <= 600
            assert r["cost"] >= 0
            assert r["description"].strip()

    def test_opening_hours_are_well_formed(self, rows: list[dict]) -> None:
        for r in rows:
            for h in r["hours"]:
                assert h["opens"] < h["closes"], r["name"]

    def test_world_places_do_not_claim_wheelchair_access(self, rows: list[dict]) -> None:
        """Nothing here was verified, so it must read as unknown, not 'yes'."""
        assert {r["accessible"] for r in rows} == {None}

    def test_derived_values_are_deterministic(self) -> None:
        assert parse_world_places() == parse_world_places()


class TestParserErrors:
    def _write(self, tmp_path: Path, body: str) -> Path:
        (tmp_path / "x.txt").write_text(body, encoding="utf-8")
        return tmp_path

    def test_rejects_unknown_city(self, tmp_path: Path) -> None:
        d = self._write(tmp_path, "=Atlantis|XX\nA|1|1|lm|hist|1|0|b\n")
        with pytest.raises(WorldDataError, match="Atlantis"):
            parse_world_places(d)

    def test_rejects_place_before_city(self, tmp_path: Path) -> None:
        d = self._write(tmp_path, "A|1|1|lm|hist|1|0|b\n")
        with pytest.raises(WorldDataError, match="before any"):
            parse_world_places(d)

    def test_rejects_bad_category_tag_and_range(self, tmp_path: Path) -> None:
        city = next(iter(load_cities()))
        for line, msg in [
            ("A|1|1|zz|hist|1|0|b", "category"),
            ("A|1|1|lm|nope|1|0|b", "tag"),
            ("A|1|1|lm|hist|9|0|b", "tier"),
            ("A|1|1|lm|hist|1|9|b", "cost"),
            ("A|95|1|lm|hist|1|0|b", "out of range"),
            ("A|1|1|lm|hist|1|0", "fields"),
            ("A|x|1|lm|hist|1|0|b", "number"),
        ]:
            d = self._write(tmp_path, f"={city}|XX\n{line}\n")
            with pytest.raises(WorldDataError, match=msg):
                parse_world_places(d)

    def test_rejects_duplicates(self, tmp_path: Path) -> None:
        city = next(iter(load_cities()))
        d = self._write(tmp_path, f"={city}|XX\nA|1|1|lm|hist|1|0|b\na|2|2|lm|hist|1|0|b\n")
        with pytest.raises(WorldDataError, match="duplicate"):
            parse_world_places(d)


def test_cities_json_is_valid() -> None:
    raw = json.loads(CITIES_FILE.read_text(encoding="utf-8"))
    for name, row in raw.items():
        assert -90 <= row["latitude"] <= 90, name
        assert -180 <= row["longitude"] <= 180, name
        assert len(row["country_code"]) == 2, name
        assert "/" in row["timezone"], name


# --------------------------------------------------------------------------- #
# Seeding, against an in-memory Mongo stand-in
# --------------------------------------------------------------------------- #


@pytest.fixture
async def mock_db(monkeypatch: pytest.MonkeyPatch):
    from beanie import init_beanie
    from mongomock_motor import AsyncMongoMockClient

    from app.models.place import Place

    client = AsyncMongoMockClient()
    database = client["world_seed_test"]
    await init_beanie(database=database, document_models=[Place])
    monkeypatch.setattr(world_seed, "get_database", lambda: database)
    # Seeding embeds through the shared re-embed, which reads the collection there.
    from app.services import place_service

    monkeypatch.setattr(place_service, "get_database", lambda: database)
    return database


class TestSeeding:
    async def test_inserts_every_place(self, mock_db) -> None:
        from app.models.place import Place

        inserted = await world_seed.seed_world_places(embed=False)
        assert inserted == EXPECTED_PLACES
        assert await Place.count() == EXPECTED_PLACES

    async def test_is_idempotent(self, mock_db) -> None:
        from app.models.place import Place

        await world_seed.seed_world_places(embed=False)
        again = await world_seed.seed_world_places(embed=False)
        assert again == 0
        assert await Place.count() == EXPECTED_PLACES

    async def test_slugs_are_unique(self, mock_db) -> None:
        await world_seed.seed_world_places(embed=False)
        slugs = await mock_db["places"].distinct("slug")
        assert len(slugs) == EXPECTED_PLACES

    async def test_does_not_overwrite_existing_places(self, mock_db) -> None:
        from app.models.place import Place

        await world_seed.seed_world_places(embed=False)
        place = await Place.find_one(Place.city == "Barcelona")
        place.description = "edited by an admin"
        await place.save()

        await world_seed.seed_world_places(embed=False)
        refreshed = await Place.get(place.id)
        assert refreshed.description == "edited by an admin"

    async def test_small_batches_insert_the_same_set(self, mock_db) -> None:
        from app.models.place import Place

        inserted = await world_seed.seed_world_places(embed=False, batch_size=7)
        assert inserted == EXPECTED_PLACES
        assert await Place.count() == EXPECTED_PLACES

    async def test_stored_fields_are_correct(self, mock_db) -> None:
        from app.models.place import Place

        await world_seed.seed_world_places(embed=False)
        place = await Place.find_one(Place.name == "Sagrada Família")
        assert place.city == "Barcelona"
        assert place.country == "Spain"
        assert place.country_code == "ES"
        assert place.timezone == "Europe/Madrid"
        assert place.category == PlaceCategory.LANDMARK
        assert place.source == "world-seed"
        assert place.cost.currency == "USD"
        assert place.wheelchair_accessible is None
        assert place.location.coordinates == [2.1744, 41.4036]  # GeoJSON is [lng, lat]

    async def test_embeddings_are_stored_when_requested(self, mock_db) -> None:
        from app.core.config import settings
        from app.models.place import Place

        await world_seed.seed_world_places(embed=True)
        place = await Place.find_one(Place.name == "Sagrada Família")
        assert place.embedding and len(place.embedding) == settings.embedding_dimensions
        # And it says which embedder made it, so search never mixes spaces.
        assert place.embedding_space == f"hash:hash-v1:{settings.embedding_dimensions}"
        assert place.embedding_model == "hash-v1"


class TestCitySummaryAtScale:
    """city_summary used to cap at 100 cities and take an arbitrary place as the centre."""

    async def test_returns_every_city(
        self, mock_db, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services import place_service

        monkeypatch.setattr(place_service, "get_database", lambda: mock_db)
        await world_seed.seed_world_places(embed=False)

        cities = await place_service.city_summary()
        assert len(cities) == EXPECTED_CITIES
        assert {c["place_count"] for c in cities} == {PLACES_PER_CITY}

    async def test_order_is_deterministic(
        self, mock_db, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services import place_service

        monkeypatch.setattr(place_service, "get_database", lambda: mock_db)
        await world_seed.seed_world_places(embed=False)

        names = [c["city"] for c in await place_service.city_summary()]
        assert names == sorted(names)  # all counts equal, so alphabetical

    async def test_centre_is_near_the_real_city(
        self, mock_db, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services import place_service

        monkeypatch.setattr(place_service, "get_database", lambda: mock_db)
        await world_seed.seed_world_places(embed=False)

        cities_meta = load_cities()
        far = []
        for c in await place_service.city_summary():
            meta = cities_meta[c["city"]]
            km = _km(meta.latitude, meta.longitude, c["latitude"], c["longitude"])
            if km > 15:
                far.append((c["city"], round(km, 1)))
        # A few cities have a deliberate far-away day trip pulling the mean off;
        # none should be more than a short drive from the true centre.
        assert far == [] or all(km < 25 for _, km in far)

    async def test_not_capped_at_one_hundred_cities(
        self, mock_db, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Core seed (3 cities) + world (100) is 103, which the old $limit cut off."""
        from app.models.base import GeoPoint
        from app.models.place import Place
        from app.services import place_service

        monkeypatch.setattr(place_service, "get_database", lambda: mock_db)
        await world_seed.seed_world_places(embed=False)
        await Place.insert_many(
            [
                Place(
                    name=f"Extra {i}",
                    slug=f"extra-{i}",
                    city=f"ExtraCity{i}",
                    country="Testland",
                    location=GeoPoint.from_lat_lng(10 + i, 10 + i),
                )
                for i in range(5)
            ]
        )

        assert len(await place_service.city_summary()) == EXPECTED_CITIES + 5
