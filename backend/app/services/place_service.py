"""Place CRUD and the three search paths: geospatial, text and semantic."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError
from slugify import slugify

from app.ai.embeddings import (
    EmbeddingUnavailableError,
    current_space,
    embed_documents,
    embed_text,
    place_embedding_text,
    vector_search,
)
from app.core.exceptions import ConflictError, NotFoundError, RateLimitError
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
        await attach_embedding(place)

    try:
        await place.insert()
    except DuplicateKeyError as exc:
        raise ConflictError("A place with that slug already exists.") from exc
    return place


def _embedding_text(document: Any) -> str:
    """The text embedded for a place, from a model or a raw document."""

    def field(name: str) -> Any:
        return document.get(name) if isinstance(document, dict) else getattr(document, name)

    return place_embedding_text(
        field("name") or "",
        field("description") or "",
        str(field("category") or ""),
        [str(tag) for tag in field("tags") or []],
        field("city") or "",
    )


async def attach_embedding(place: Place) -> bool:
    """Embed ``place`` in the current space. Returns whether it got a vector.

    A provider failure does not fail the save: the place is stored without a
    vector (never with one for text that has since changed) and the next
    re-embed picks it up. Until then it is simply absent from semantic search.
    """
    try:
        vector = await embed_text(_embedding_text(place))
    except (EmbeddingUnavailableError, RateLimitError) as exc:
        place.embedding = None
        place.embedding_model = None
        place.embedding_space = None
        logger.warning("place_embedding_deferred", slug=place.slug, reason=exc.message)
        return False
    space = current_space()
    place.embedding = vector
    place.embedding_model = space.model
    place.embedding_space = space.key
    return True


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

    if {"name", "description", "category", "tags", "city"} & set(data):
        await attach_embedding(place)

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


@dataclass(slots=True)
class ReembedReport:
    space: str
    written: int
    remaining: int
    # Why it stopped early (rate limit, provider down), or None when it finished.
    stopped: str | None = None


async def reembed_places(
    *, batch_size: int | None = None, limit: int | None = None, dry_run: bool = False
) -> ReembedReport:
    """Embed every place whose vector is missing or from another space.

    Idempotent and resumable: it selects only places not already in the current
    space, so a second run does nothing and an interrupted run carries on where
    it stopped. A rate limit that outlasts the provider retries ends the run
    early with the counts so far, instead of failing what was already written.
    """
    space = current_space()
    collection = get_database()["places"]
    stale: dict[str, Any] = {
        "$or": [{"embedding_space": {"$ne": space.key}}, {"embedding": None}]
    }
    size = batch_size or 200
    if dry_run:
        return ReembedReport(space.key, 0, await collection.count_documents(stale))

    written = 0
    stopped: str | None = None
    projection = {"name": 1, "description": 1, "category": 1, "tags": 1, "city": 1}
    while limit is None or written < limit:
        take = size if limit is None else min(size, limit - written)
        documents = await collection.find(stale, projection).limit(take).to_list(take)
        if not documents:
            break
        try:
            vectors = await embed_documents([_embedding_text(d) for d in documents])
        except (EmbeddingUnavailableError, RateLimitError) as exc:
            stopped = exc.message
            break
        results = await asyncio.gather(
            *(
                collection.update_one(
                    # Re-checked, so a concurrent run never overwrites newer work.
                    {"_id": d["_id"], "embedding_space": {"$ne": space.key}},
                    {
                        "$set": {
                            "embedding": vector,
                            "embedding_model": space.model,
                            "embedding_space": space.key,
                        }
                    },
                )
                for d, vector in zip(documents, vectors, strict=True)
            )
        )
        modified = sum(r.modified_count for r in results)
        if modified == 0:  # another run took them; avoid spinning
            break
        written += modified

    remaining = await collection.count_documents(stale)
    logger.info(
        "places_reembedded",
        space=space.key,
        written=written,
        remaining=remaining,
        stopped=stopped,
    )
    return ReembedReport(space.key, written, remaining, stopped)


async def backfill_embeddings(*, batch_size: int = 200) -> int:
    """Re-embed what needs it. Returns how many were written."""
    return (await reembed_places(batch_size=batch_size)).written


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
