"""A worldwide corpus of tourist places: 100 cities x 20 places each.

The hand-written ``app.db.seed`` corpus covers three cities in detail. This module
adds breadth: a curated shortlist of each city's best-known places, stored as
compact text under ``world_data/`` so the whole set stays reviewable in a diff.

File format (one ``*.txt`` per region)::

    =Barcelona|ES
    Sagrada Família|41.4036|2.1744|lm|arch,cult|1|3|Gaudí's unfinished basilica[|minutes]

A ``=City|CC`` line opens a city block; every following line is a place:
``name|lat|lng|category|tags|tier|cost|blurb`` with an optional trailing visit
length in minutes.

What is and is not real data
----------------------------
* City centres, countries and time zones come from GeoNames (see
  ``scripts/build_world_cities.py``) and are stored in ``world_data/cities.json``.
* Place names and coordinates are curated by hand. Coordinates are accurate to
  roughly a few hundred metres, which is right for city-scale route planning and
  not for turn-by-turn navigation. Verify before publishing anything.
* ``tier`` (1 = must-see, 2 = major, 3 = worth a detour) and ``cost`` (0-3) are
  editorial judgements. Ratings, popularity and prices are *derived* from them
  and are estimates, not review scores or price lists.
* Opening hours are category-level approximations (see ``_hours_for``), with no
  weekday closures invented for any specific venue.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.db.mongodb import get_database
from app.models.enums import InterestTag, PlaceCategory
from app.observability.logging import get_logger

logger = get_logger(__name__)

DATA_DIR = Path(__file__).parent / "world_data"
CITIES_FILE = DATA_DIR / "cities.json"
SOURCE_NAME = "world-seed"

CATEGORY_CODES: dict[str, PlaceCategory] = {
    "lm": PlaceCategory.LANDMARK,
    "mu": PlaceCategory.MUSEUM,
    "pk": PlaceCategory.PARK,
    "rs": PlaceCategory.RESTAURANT,
    "cf": PlaceCategory.CAFE,
    "br": PlaceCategory.BAR,
    "sh": PlaceCategory.SHOPPING,
    "bc": PlaceCategory.BEACH,
    "vp": PlaceCategory.VIEWPOINT,
    "rl": PlaceCategory.RELIGIOUS,
    "nl": PlaceCategory.NIGHTLIFE,
    "en": PlaceCategory.ENTERTAINMENT,
    "nt": PlaceCategory.NATURE,
    "at": PlaceCategory.ATTRACTION,
}

TAG_CODES: dict[str, InterestTag] = {
    "hist": InterestTag.HISTORY,
    "art": InterestTag.ART,
    "food": InterestTag.FOOD,
    "nat": InterestTag.NATURE,
    "adv": InterestTag.ADVENTURE,
    "night": InterestTag.NIGHTLIFE,
    "shop": InterestTag.SHOPPING,
    "relax": InterestTag.RELAXATION,
    "fam": InterestTag.FAMILY,
    "photo": InterestTag.PHOTOGRAPHY,
    "arch": InterestTag.ARCHITECTURE,
    "cult": InterestTag.CULTURE,
    "sport": InterestTag.SPORTS,
    "music": InterestTag.MUSIC,
}

# Typical time spent, in minutes, when a row does not say otherwise.
DEFAULT_MINUTES: dict[PlaceCategory, int] = {
    PlaceCategory.LANDMARK: 60,
    PlaceCategory.MUSEUM: 120,
    PlaceCategory.PARK: 90,
    PlaceCategory.RESTAURANT: 75,
    PlaceCategory.CAFE: 45,
    PlaceCategory.BAR: 90,
    PlaceCategory.SHOPPING: 90,
    PlaceCategory.BEACH: 150,
    PlaceCategory.VIEWPOINT: 45,
    PlaceCategory.RELIGIOUS: 45,
    PlaceCategory.NIGHTLIFE: 120,
    PlaceCategory.ENTERTAINMENT: 120,
    PlaceCategory.NATURE: 180,
    PlaceCategory.ATTRACTION: 90,
}

# Approximate per-person cost in USD for each cost tier (0 = free).
COST_TIER_USD = {0: 0.0, 1: 8.0, 2: 20.0, 3: 40.0}
TIER_RATING = {1: 4.7, 2: 4.5, 3: 4.3}
TIER_POPULARITY = {1: 0.92, 2: 0.72, 3: 0.52}

# Genuine day-trip highlights that sit further from their city centre than the
# validator's hard limit. Everything else that far out is treated as a typo.
ALLOWED_FAR: frozenset[tuple[str, str]] = frozenset({("Cusco", "Machu Picchu")})

_PAID_SIGHTS = {
    PlaceCategory.LANDMARK,
    PlaceCategory.ATTRACTION,
    PlaceCategory.RELIGIOUS,
    PlaceCategory.ENTERTAINMENT,
}


@dataclass(frozen=True, slots=True)
class WorldCity:
    name: str
    country: str
    country_code: str
    timezone: str
    latitude: float
    longitude: float


class WorldDataError(ValueError):
    """Raised when the data files are malformed, with file and line context."""


@lru_cache(maxsize=1)
def load_cities() -> dict[str, WorldCity]:
    """City metadata keyed by display name, from ``cities.json``."""
    raw = json.loads(CITIES_FILE.read_text(encoding="utf-8"))
    return {
        name: WorldCity(
            name=name,
            country=row["country"],
            country_code=row["country_code"],
            timezone=row["timezone"],
            latitude=row["latitude"],
            longitude=row["longitude"],
        )
        for name, row in raw.items()
    }


def _hours_for(category: PlaceCategory, cost_tier: int) -> list[dict[str, Any]]:
    """Category-level opening hours. Empty means "assume open"."""

    def daily(opens: str, closes: str) -> list[dict[str, Any]]:
        return [{"day_of_week": d, "opens": opens, "closes": closes} for d in range(7)]

    if category is PlaceCategory.MUSEUM:
        return daily("09:30", "17:30")
    if category is PlaceCategory.RESTAURANT:
        return daily("11:30", "22:30")
    if category is PlaceCategory.CAFE:
        return daily("08:00", "20:00")
    if category in (PlaceCategory.BAR, PlaceCategory.NIGHTLIFE):
        return daily("19:00", "23:59")
    if category is PlaceCategory.SHOPPING:
        return daily("10:00", "20:00")
    if category in _PAID_SIGHTS and cost_tier > 0:
        return daily("09:00", "18:00")
    return []


def _jitter(key: str, spread: float) -> float:
    """Deterministic value in ``[-spread, +spread]`` derived from ``key``.

    Keeps derived ratings from being identical across a tier while staying
    stable between runs, which matters for reproducible seeding and tests.
    """
    return ((zlib.crc32(key.encode("utf-8")) % 2001) / 1000.0 - 1.0) * spread


def _parse_line(source: str, number: int, line: str, city: WorldCity) -> dict[str, Any]:
    where = f"{source}:{number}"
    parts = [p.strip() for p in line.split("|")]
    if len(parts) not in (8, 9):
        raise WorldDataError(f"{where}: expected 8 or 9 fields, got {len(parts)}: {line!r}")

    name, lat_s, lng_s, cat_s, tags_s, tier_s, cost_s, blurb = parts[:8]
    try:
        lat, lng = float(lat_s), float(lng_s)
        tier, cost_tier = int(tier_s), int(cost_s)
        minutes_override = int(parts[8]) if len(parts) == 9 and parts[8] else None
    except ValueError as exc:
        raise WorldDataError(f"{where}: bad number in {line!r}") from exc

    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise WorldDataError(f"{where}: coordinates out of range for {name!r}")
    if cat_s not in CATEGORY_CODES:
        raise WorldDataError(f"{where}: unknown category {cat_s!r}")
    if tier not in TIER_RATING:
        raise WorldDataError(f"{where}: tier must be 1-3, got {tier}")
    if cost_tier not in COST_TIER_USD:
        raise WorldDataError(f"{where}: cost must be 0-3, got {cost_tier}")
    if not name or not blurb:
        raise WorldDataError(f"{where}: name and blurb are required")

    tags: list[InterestTag] = []
    for code in filter(None, (t.strip() for t in tags_s.split(","))):
        if code not in TAG_CODES:
            raise WorldDataError(f"{where}: unknown tag {code!r}")
        tags.append(TAG_CODES[code])

    category = CATEGORY_CODES[cat_s]
    jitter_key = f"{name}|{city.name}"
    rating = round(min(5.0, TIER_RATING[tier] + _jitter(jitter_key, 0.15)), 1)
    popularity = round(
        min(1.0, max(0.05, TIER_POPULARITY[tier] + _jitter(jitter_key + "#p", 0.06))), 2
    )

    return {
        "name": name,
        "city": city.name,
        "country": city.country,
        "country_code": city.country_code,
        "lat": lat,
        "lng": lng,
        "category": category,
        "tags": tags,
        "rating": rating,
        "popularity": popularity,
        "minutes": minutes_override or DEFAULT_MINUTES[category],
        "cost": COST_TIER_USD[cost_tier],
        "currency": "USD",
        "hours": _hours_for(category, cost_tier),
        "description": blurb,
        "timezone": city.timezone,
        # Unknown, not "yes": nothing in this corpus was checked for step-free access.
        "accessible": None,
        "family": category not in (PlaceCategory.BAR, PlaceCategory.NIGHTLIFE),
        "tier": tier,
    }


def parse_world_places(data_dir: Path | None = None) -> list[dict[str, Any]]:
    """Parse every data file into place rows, in file order.

    Raises:
        WorldDataError: on any malformed line, unknown city or duplicate place.
    """
    directory = data_dir or DATA_DIR
    cities = load_cities()
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for path in sorted(directory.glob("*.txt")):
        current: WorldCity | None = None
        for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("="):
                city_name = line[1:].split("|")[0].strip()
                if city_name not in cities:
                    raise WorldDataError(
                        f"{path.name}:{number}: {city_name!r} is missing from cities.json; "
                        "run scripts/build_world_cities.py"
                    )
                current = cities[city_name]
                continue

            if current is None:
                raise WorldDataError(f"{path.name}:{number}: place before any =City line")

            row = _parse_line(path.name, number, line, current)
            key = (row["city"], row["name"].casefold())
            if key in seen:
                raise WorldDataError(f"{path.name}:{number}: duplicate {row['name']!r}")
            seen.add(key)
            rows.append(row)

    return rows


async def seed_world_places(*, embed: bool = True, batch_size: int = 250) -> int:
    """Insert the world corpus. Places that already exist (by slug) are skipped.

    Batched, because inserting 2,000 documents one round trip at a time is the
    difference between seconds and minutes. Safe to re-run.

    Returns:
        The number of places inserted.
    """
    from slugify import slugify

    from app.models.base import GeoPoint
    from app.models.place import OpeningHours, Place, PlaceCost

    rows = parse_world_places()
    existing = set(await get_database()["places"].distinct("slug"))

    pending: list[Place] = []
    inserted = 0

    async def flush() -> None:
        nonlocal inserted
        if pending:
            await Place.insert_many(list(pending))
            inserted += len(pending)
            pending.clear()

    for row in rows:
        slug = slugify(f"{row['name']}-{row['city']}")[:280]
        if slug in existing:
            continue
        existing.add(slug)

        place = Place(
            name=row["name"],
            slug=slug,
            description=row["description"],
            category=row["category"],
            tags=row["tags"],
            location=GeoPoint.from_lat_lng(row["lat"], row["lng"]),
            city=row["city"],
            country=row["country"],
            country_code=row["country_code"],
            timezone=row["timezone"],
            rating=row["rating"],
            review_count=int(row["popularity"] * 50_000),
            popularity=row["popularity"],
            average_visit_minutes=row["minutes"],
            cost=PlaceCost(amount=row["cost"], currency=row["currency"]),
            opening_hours=[OpeningHours(**h) for h in row["hours"]],
            wheelchair_accessible=row["accessible"],
            family_friendly=row["family"],
            source=SOURCE_NAME,
        )
        pending.append(place)
        if len(pending) >= batch_size:
            await flush()

    await flush()
    if embed:
        from app.services.place_service import reembed_places

        await reembed_places()
    logger.info("world_places_seeded", inserted=inserted, total=len(rows))
    return inserted
