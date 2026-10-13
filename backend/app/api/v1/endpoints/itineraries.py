"""Itinerary read, edit and optimisation endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import CurrentUser
from app.schemas.common import Message, Page, PaginationDep
from app.schemas.itinerary import (
    AlgorithmInfo,
    CompareRouteRequest,
    CompareRouteResponse,
    ItineraryPublic,
    ItinerarySummary,
    OptimizeRouteRequest,
    OptimizeRouteResponse,
    ReorderDayRequest,
)
from app.services import itinerary_service

router = APIRouter(prefix="/itineraries", tags=["itineraries"])


@router.post(
    "/optimize-route",
    response_model=OptimizeRouteResponse,
    summary="Optimise an arbitrary set of places",
    description=(
        "Plans the best order for a list of place ids without needing a saved "
        "trip. Choose the algorithm with `algorithm` (default: the server "
        "setting, normally OR-Tools). Places that cannot be fitted are returned "
        "in `dropped_place_ids` rather than causing a failure."
    ),
)
async def optimize_route(
    payload: OptimizeRouteRequest, user: CurrentUser
) -> OptimizeRouteResponse:
    return await itinerary_service.optimize_places(payload)


@router.post(
    "/optimize-route/compare",
    response_model=CompareRouteResponse,
    summary="Compare route-optimisation algorithms on the same places",
    description=(
        "Runs several algorithms on the same places and returns each plan with its "
        "objective (minutes, lower is better), its gap to the best plan, and how "
        "long it took. Every objective comes from one shared evaluator, so the "
        "numbers are directly comparable."
    ),
)
async def compare_route_algorithms(
    payload: CompareRouteRequest, user: CurrentUser
) -> CompareRouteResponse:
    return await itinerary_service.compare_route_algorithms(payload)


@router.get(
    "/optimizer/algorithms",
    response_model=list[AlgorithmInfo],
    summary="List the available route-optimisation algorithms",
)
async def list_algorithms(user: CurrentUser) -> list[AlgorithmInfo]:
    return itinerary_service.list_optimizer_algorithms()


@router.get(
    "",
    response_model=Page[ItinerarySummary],
    summary="List the caller's itineraries",
)
async def list_itineraries(
    user: CurrentUser,
    pagination: PaginationDep,
) -> Page[ItinerarySummary]:
    from app.models.itinerary import Itinerary

    total = await Itinerary.find(Itinerary.user_id == user.id).count()
    items = (
        await Itinerary.find(Itinerary.user_id == user.id)
        .sort(-Itinerary.created_at)
        .skip(pagination.skip)
        .limit(pagination.limit)
        .to_list()
    )
    return Page.build(
        [
            ItinerarySummary(
                id=i.id,  # type: ignore[arg-type]
                trip_id=i.trip_id,
                version=i.version,
                status=i.status,
                title=i.title,
                total_estimated_cost=i.total_estimated_cost,
                currency=i.currency,
                total_distance_km=i.total_distance_km,
                day_count=len(i.days),
                created_at=i.created_at,
            )
            for i in items
        ],
        total,
        pagination.skip,
        pagination.limit,
    )


@router.get("/{itinerary_id}", response_model=ItineraryPublic, summary="Get one itinerary")
async def get_itinerary(itinerary_id: str, user: CurrentUser) -> ItineraryPublic:
    return ItineraryPublic.model_validate(
        await itinerary_service.get_owned_itinerary(itinerary_id, user)
    )


@router.get("/{itinerary_id}/stats", summary="Derived figures for an itinerary")
async def get_stats(itinerary_id: str, user: CurrentUser) -> dict:
    itinerary = await itinerary_service.get_owned_itinerary(itinerary_id, user)
    return await itinerary_service.itinerary_stats(itinerary)


@router.post(
    "/{itinerary_id}/reorder",
    response_model=ItineraryPublic,
    summary="Reorder one day's activities",
    description=(
        "Send the new sequence of activity indices; all times, travel legs and "
        "totals are recomputed server-side so the stored plan stays consistent."
    ),
)
async def reorder(
    itinerary_id: str, payload: ReorderDayRequest, user: CurrentUser
) -> ItineraryPublic:
    itinerary = await itinerary_service.get_owned_itinerary(itinerary_id, user)
    return ItineraryPublic.model_validate(
        await itinerary_service.reorder_day(itinerary, payload)
    )


@router.delete(
    "/{itinerary_id}/days/{day_number}/activities/{activity_index}",
    response_model=ItineraryPublic,
    summary="Remove one activity and re-flow the day",
)
async def remove_activity(
    itinerary_id: str, day_number: int, activity_index: int, user: CurrentUser
) -> ItineraryPublic:
    itinerary = await itinerary_service.get_owned_itinerary(itinerary_id, user)
    return ItineraryPublic.model_validate(
        await itinerary_service.remove_activity(itinerary, day_number, activity_index)
    )


@router.post(
    "/{itinerary_id}/activate",
    response_model=Message,
    summary="Make this version the trip's active itinerary",
)
async def activate(itinerary_id: str, user: CurrentUser) -> Message:
    from app.services import trip_service

    itinerary = await itinerary_service.get_owned_itinerary(itinerary_id, user)
    trip = await trip_service.get_owned_trip(itinerary.trip_id, user)
    await itinerary_service.set_active(trip, itinerary)
    return Message(
        message="Active itinerary updated.",
        details={"trip_id": str(trip.id), "version": itinerary.version},
    )


@router.delete(
    "/{itinerary_id}", response_model=Message, summary="Delete an itinerary version"
)
async def delete_itinerary(itinerary_id: str, user: CurrentUser) -> Message:
    itinerary = await itinerary_service.get_owned_itinerary(itinerary_id, user)
    await itinerary_service.delete_itinerary(itinerary)
    return Message(message="Itinerary deleted.")
