"""Place CRUD and the three search paths: geospatial, text and semantic."""

from __future__ import annotations

from typing import Any

from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError
from slugify import slugify

from app.ai.embeddings import (
    place_embedding_text,
    vector_search,
)
from app.core.exceptions import ConflictError, NotFoundError
from app.db.mongodb import get_database
from app.db.sorting import descending
from app.models.base import GeoPoint
from app.models.enums import PlaceCategory
from app.models.place import Place
from app.observability.logging import get_logger
from app.optimization.distance import Coordinate, haversine_km
from app.schemas.place import PlaceCreate, PlaceUpdate

logger = get_logger(__name__)


async def _unique_slug(name: str, city: str) -> str:
    """Build a stable slug, disambiguating collisions with a counter."""
    base = slugify(f"{name}-{city}")[:280] or "place"
    candidate = base
    suffix = 2
    while await Place.find_one(Place.slug == candidate) is not None:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


async def create_place(payload: PlaceCreate, *, embed: bool = True) -> Place:
    """Create a place and, by default, compute its embedding immediately.

    Raises:
        ConflictError: when the generated slug is already taken.
    """
    place = Place(
        name=payload.name,
        slug=await _unique_slug(payload.name, payload.city),
        description=payload.description,
        category=payload.category,
        tags=payload.tags,
        location=payload.to_geo_point(),
        address=payload.address,
        city=payload.city,
        country=payload.country,
        country_code=payload.country_code,
        timezone=payload.timezone,
        rating=payload.rating,
        review_count=payload.review_count,
        popularity=payload.popularity,
        average_visit_minutes=payload.average_visit_minutes,
        cost=payload.cost,
        opening_hours=payload.opening_hours,
        wheelchair_accessible=payload.wheelchair_accessible,
        family_friendly=payload.family_friendly,
        image_url=payload.image_url,
        website=payload.website,
        source="api",
    )
    if embed:
        await _attach_embedding(place)

    try:
        await place.insert()
    except DuplicateKeyError as exc:
        raise ConflictError("A place with that slug already exists.") from exc
    return place


async def _attach_embedding(place: Place) -> None:
    from app.ai.embeddings import embed_text
    from app.core.config import settings

    text = place_embedding_text(
        place.name,
        place.description,
        str(place.category),
        [str(t) for t in place.tags],
        place.city,
    )
    place.embedding = await embed_text(text)
    place.embedding_model = settings.embedding_model


async def get_place(place_id: str | PydanticObjectId) -> Place:
    """Fetch one place.

    Raises:
        NotFoundError: when the id is malformed or no such place exists.
    """
    try:
        oid = PydanticObjectId(str(place_id))
    except Exception as exc:
        raise NotFoundError("Place not found.") from exc

    place = await Place.get(oid)
    if place is None:
        raise NotFoundError("Place not found.")
    return place


async def update_place(place_id: str | PydanticObjectId, payload: PlaceUpdate) -> Place:
    """Apply a partial update, re-embedding when the searchable text changed."""
    place = await get_place(place_id)
    data = payload.model_dump(exclude_unset=True)

    latitude = data.pop("latitude", None)
    longitude = data.pop("longitude", None)
    if latitude is not None and longitude is not None:
        place.location = GeoPoint.from_lat_lng(latitude, longitude)

    for field, value in data.items():
        setattr(place, field, value)

    if {"name", "description", "category", "tags"} & set(data):
        await _attach_embedding(place)

    await place.save()
    return place


async def delete_place(place_id: str | PydanticObjectId, *, hard: bool = False) -> None:
    """Remove a place.

    A soft delete is the default: itineraries reference places by id, and hard
    deletion would leave those references dangling.
    """
    place = await get_place(place_id)
    if hard:
        await place.delete()
    else:
        place.is_active = False
        await place.save()


async def list_places(
    *,
    city: str | None = None,
    category: PlaceCategory | None = None,
    tags: list[str] | None = None,
    min_rating: float = 0.0,
    include_inactive: bool = False,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[Place], int]:
    """Filtered listing. Returns the page and the total match count."""
    query: dict[str, Any] = {}
    if not include_inactive:
        query["is_active"] = True
    if city:
        query["city"] = city
    if category:
        query["category"] = category
    if tags:
        query["tags"] = {"$in": tags}
    if min_rating > 0:
        query["rating"] = {"$gte": min_rating}

    total = await Place.find(query).count()
    items = (
        await Place.find(query)
        .sort(descending(Place.rating), descending(Place.popularity))
        .skip(skip)
        .limit(limit)
        .to_list()
    )
    return items, total


async def find_nearby(
    latitude: float,
    longitude: float,
    *,
    radius_km: float = 5.0,
    category: PlaceCategory | None = None,
    min_rating: float = 0.0,
    limit: int = 20,
) -> list[tuple[Place, float]]:
    """Radius search via the ``2dsphere`` index.

    ``$nearSphere`` returns results already sorted by distance, so no client-side
    sort is needed; the distance is recomputed here only to report it.
    """
    query: dict[str, Any] = {
        "is_active": True,
        "location": {
            "$nearSphere": {
                "$geometry": {"type": "Point", "coordinates": [longitude, latitude]},
                "$maxDistance": int(radius_km * 1000),
            }
        },
    }
    if category:
        query["category"] = category
    if min_rating > 0:
        query["rating"] = {"$gte": min_rating}

    places = await Place.find(query).limit(limit).to_list()
    origin = Coordinate(latitude, longitude)
    return [
        (place, round(haversine_km(origin, Coordinate(*place.location.as_lat_lng())), 3))
        for place in places
    ]


async def text_search(
    query: str, *, city: str | None = None, limit: int = 20
) -> list[tuple[Place, float]]:
    """Keyword search over the compound text index, ranked by MongoDB's score."""
    filters: dict[str, Any] = {"$text": {"$search": query}, "is_active": True}
    if city:
        filters["city"] = city

    database = get_database()
    cursor = (
        database["places"]
        .find(filters, {"score": {"$meta": "textScore"}, "embedding": 0})
        .sort([("score", {"$meta": "textScore"})])
        .limit(limit)
    )
    documents = await cursor.to_list(length=limit)

    results: list[tuple[Place, float]] = []
    for document in documents:
        score = float(document.pop("score", 0.0))
        document["id"] = document.pop("_id")
        results.append((Place.model_validate(document), round(score, 4)))
    return results


async def semantic_search(
    query: str, *, city: str | None = None, limit: int = 20
) -> list[tuple[Place, float]]:
    """Embedding search, via Atlas Vector Search or the cosine fallback."""
    documents = await vector_search(query, city=city, limit=limit)
    results: list[tuple[Place, float]] = []
    for document in documents:
        score = float(document.pop("search_score", 0.0))
        payload = dict(document)
        if "_id" in payload:
            payload["id"] = payload.pop("_id")
        results.append((Place.model_validate(payload), round(score, 6)))
    return results


async def backfill_embeddings(*, batch_size: int = 200) -> int:
    """Embed every place that is missing one. Returns how many were written."""
    from app.ai.pipeline import ensure_place_embedding

    written = 0
    while True:
        batch = await Place.find({"embedding": None}).limit(batch_size).to_list()
        if not batch:
            break
        for place in batch:
            if await ensure_place_embedding(place):
                written += 1
    logger.info("embeddings_backfilled", count=written)
    return written


MAX_CITIES = 500  # comfortably above the 100 cities in the world corpus


async def city_summary() -> list[dict[str, Any]]:
    """Per-city place counts, average rating and centre, for destination pickers.

    A city's centre is the mean of its places' coordinates: stable, and close
    enough to the middle for choosing where a day starts. (Averaging longitudes is
    wrong across the antimeridian; no city crosses it today.)
    """
    database = get_database()
    pipeline: list[dict[str, Any]] = [
        {"$match": {"is_active": True}},
        {
            "$group": {
                "_id": {"city": "$city", "country": "$country"},
                "place_count": {"$sum": 1},
                "average_rating": {"$avg": "$rating"},
                "latitude": {"$avg": {"$arrayElemAt": ["$location.coordinates", 1]}},
                "longitude": {"$avg": {"$arrayElemAt": ["$location.coordinates", 0]}},
            }
        },
        # City name as a tie-break keeps the order deterministic when many cities
        # have the same number of places.
        {"$sort": {"place_count": -1, "_id.city": 1}},
        {"$limit": MAX_CITIES},
    ]
    rows = await database["places"].aggregate(pipeline).to_list(length=MAX_CITIES)
    return [
        {
            "city": row["_id"]["city"],
            "country": row["_id"]["country"],
            "place_count": row["place_count"],
            "average_rating": round(row["average_rating"] or 0.0, 2),
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
        }
        for row in rows
    ]
