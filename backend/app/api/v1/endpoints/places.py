"""Place discovery and administration endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import AdminUser
from app.models.enums import PlaceCategory
from app.schemas.common import Message, Page, PaginationDep
from app.schemas.place import (
    NearbyQuery,
    PlaceCreate,
    PlacePublic,
    PlaceUpdate,
    SemanticSearchRequest,
)
from app.services import place_service

router = APIRouter(prefix="/places", tags=["places"])


@router.get("", response_model=Page[PlacePublic], summary="List and filter places")
async def list_places(
    pagination: PaginationDep,
    city: Annotated[str | None, Query(max_length=200)] = None,
    category: PlaceCategory | None = None,
    tags: Annotated[list[str] | None, Query()] = None,
    min_rating: Annotated[float, Query(ge=0, le=5)] = 0.0,
) -> Page[PlacePublic]:
    items, total = await place_service.list_places(
        city=city,
        category=category,
        tags=tags,
        min_rating=min_rating,
        skip=pagination.skip,
        limit=pagination.limit,
    )
    return Page.build(
        [PlacePublic.model_validate(p) for p in items],
        total,
        pagination.skip,
        pagination.limit,
    )


@router.get(
    "/nearby",
    response_model=list[PlacePublic],
    summary="Find places within a radius",
    description=(
        "Backed by MongoDB's 2dsphere index. Results come back ordered by "
        "distance, which is returned on each item as `distance_km`."
    ),
)
async def nearby(params: Annotated[NearbyQuery, Depends()]) -> list[PlacePublic]:
    pairs = await place_service.find_nearby(
        params.latitude,
        params.longitude,
        radius_km=params.radius_km,
        category=params.category,
        min_rating=params.min_rating,
        limit=params.limit,
    )
    results = []
    for place, distance in pairs:
        item = PlacePublic.model_validate(place)
        item.distance_km = distance
        results.append(item)
    return results


@router.get(
    "/search",
    response_model=list[PlacePublic],
    summary="Keyword search",
    description="Full-text search over name, description and city.",
)
async def search(
    q: Annotated[str, Query(min_length=2, max_length=200)],
    city: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[PlacePublic]:
    pairs = await place_service.text_search(q, city=city, limit=limit)
    results = []
    for place, score in pairs:
        item = PlacePublic.model_validate(place)
        item.search_score = score
        results.append(item)
    return results


@router.post(
    "/semantic-search",
    response_model=list[PlacePublic],
    summary="Semantic search over place embeddings",
    description=(
        "Uses Atlas Vector Search when the deployment supports it, and an "
        "in-process cosine similarity scan otherwise. Unlike keyword search, "
        "this matches meaning: 'somewhere quiet to read' can surface a library "
        "or a garden."
    ),
)
async def semantic_search(payload: SemanticSearchRequest) -> list[PlacePublic]:
    pairs = await place_service.semantic_search(
        payload.query, city=payload.city, limit=payload.limit
    )
    results = []
    for place, score in pairs:
        item = PlacePublic.model_validate(place)
        item.search_score = score
        results.append(item)
    return results


@router.get("/cities", summary="Destinations with place coverage")
async def cities() -> list[dict]:
    return await place_service.city_summary()


@router.get("/{place_id}", response_model=PlacePublic, summary="Get one place")
async def get_place(place_id: str) -> PlacePublic:
    return PlacePublic.model_validate(await place_service.get_place(place_id))


@router.post(
    "",
    response_model=PlacePublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a place (admin only)",
)
async def create_place(payload: PlaceCreate, admin: AdminUser) -> PlacePublic:
    return PlacePublic.model_validate(await place_service.create_place(payload))


@router.patch(
    "/{place_id}", response_model=PlacePublic, summary="Update a place (admin only)"
)
async def update_place(
    place_id: str, payload: PlaceUpdate, admin: AdminUser
) -> PlacePublic:
    return PlacePublic.model_validate(await place_service.update_place(place_id, payload))


@router.delete(
    "/{place_id}",
    response_model=Message,
    summary="Deactivate a place (admin only)",
    description=(
        "Soft-deletes by default, because saved itineraries reference places by "
        "id. Pass `hard=true` to remove the document outright."
    ),
)
async def delete_place(
    place_id: str,
    admin: AdminUser,
    hard: Annotated[bool, Query()] = False,
) -> Message:
    await place_service.delete_place(place_id, hard=hard)
    return Message(message="Place removed." if hard else "Place deactivated.")


@router.post(
    "/admin/backfill-embeddings",
    response_model=Message,
    summary="Embed every place not yet in the configured embedding space (admin only)",
)
async def backfill_embeddings(admin: AdminUser) -> Message:
    report = await place_service.reembed_places()
    return Message(
        message=(
            "Backfill complete."
            if report.stopped is None
            else f"Backfill stopped early: {report.stopped} Run it again to continue."
        ),
        details={
            "embedded": report.written,
            "remaining": report.remaining,
            "space": report.space,
        },
    )
