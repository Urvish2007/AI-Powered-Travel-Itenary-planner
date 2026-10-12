"""Trip endpoints, including itinerary generation."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.config import settings
from app.core.deps import CurrentUser
from app.core.exceptions import ExternalServiceError
from app.models.enums import TripStatus
from app.schemas.common import Message, Page, PaginationDep
from app.schemas.itinerary import ItineraryPublic, ItinerarySummary
from app.schemas.trip import (
    GenerateItineraryRequest,
    TaskAccepted,
    TaskStatus,
    TripCreate,
    TripPublic,
    TripUpdate,
)
from app.services import itinerary_service, trip_service

router = APIRouter(prefix="/trips", tags=["trips"])


@router.post(
    "",
    response_model=TripPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a trip",
    description=(
        "Constraints left unset are seeded from the caller's saved travel "
        "preferences, so a returning traveller does not restate them."
    ),
)
async def create_trip(payload: TripCreate, user: CurrentUser) -> TripPublic:
    trip = await trip_service.create_trip(user, payload)
    return _to_public(trip)


@router.get("", response_model=Page[TripPublic], summary="List the caller's trips")
async def list_trips(
    user: CurrentUser,
    pagination: PaginationDep,
    trip_status: Annotated[TripStatus | None, Query(alias="status")] = None,
) -> Page[TripPublic]:
    items, total = await trip_service.list_trips(
        user, status=trip_status, skip=pagination.skip, limit=pagination.limit
    )
    return Page.build(
        [_to_public(t) for t in items], total, pagination.skip, pagination.limit
    )


@router.get("/{trip_id}", response_model=TripPublic, summary="Get one trip")
async def get_trip(trip_id: str, user: CurrentUser) -> TripPublic:
    return _to_public(await trip_service.get_owned_trip(trip_id, user))


@router.patch("/{trip_id}", response_model=TripPublic, summary="Update a trip")
async def update_trip(trip_id: str, payload: TripUpdate, user: CurrentUser) -> TripPublic:
    trip = await trip_service.get_owned_trip(trip_id, user)
    return _to_public(await trip_service.update_trip(trip, payload))


@router.delete("/{trip_id}", response_model=Message, summary="Delete a trip")
async def delete_trip(trip_id: str, user: CurrentUser) -> Message:
    trip = await trip_service.get_owned_trip(trip_id, user)
    await trip_service.delete_trip(trip)
    return Message(message="Trip deleted, along with its itineraries.")


@router.post(
    "/{trip_id}/generate",
    response_model=ItineraryPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Generate an itinerary for a trip",
    description=(
        "Runs the full pipeline: the brief is parsed by an LLM, candidate places "
        "are recalled geospatially and semantically, scored for this traveller, "
        "scheduled by OR-Tools under time-window and pace constraints, then "
        "narrated. Each run is saved as a new version.\n\n"
        "Returns 422 when no feasible schedule exists under the trip's constraints."
    ),
)
async def generate(
    trip_id: str, payload: GenerateItineraryRequest, user: CurrentUser
) -> ItineraryPublic:
    trip = await trip_service.get_owned_trip(trip_id, user)
    itinerary = await itinerary_service.generate_itinerary(
        trip, brief=payload.brief, regenerate=payload.regenerate
    )
    return ItineraryPublic.model_validate(itinerary)


@router.post(
    "/{trip_id}/generate-async",
    response_model=TaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue itinerary generation on a worker",
    description=(
        "Same pipeline as `/generate`, but queued on a Celery worker instead of "
        "held open for the duration of the solve. Use this for long trips, bulk "
        "re-planning, or any client that should not block.\n\n"
        "Returns 503 when no broker is reachable - background processing requires "
        "Redis and a running worker."
    ),
    responses={503: {"description": "No Celery broker is reachable."}},
)
async def generate_async(
    trip_id: str, payload: GenerateItineraryRequest, user: CurrentUser
) -> TaskAccepted:
    trip = await trip_service.get_owned_trip(trip_id, user)

    # Imported lazily so that a missing or misconfigured broker cannot break
    # importing the module - and therefore the whole synchronous API.
    from app.workers.tasks import generate_itinerary_task

    try:
        task = generate_itinerary_task.delay(
            str(trip.id), payload.brief, payload.regenerate or True
        )
    except Exception as exc:  # broker unreachable
        raise ExternalServiceError(
            "Background processing is unavailable: no Celery broker could be "
            "reached. Use POST /generate instead, or start Redis and a worker.",
            details={"reason": str(exc)[:200]},
        ) from exc

    return TaskAccepted(
        task_id=task.id,
        trip_id=str(trip.id),
        poll_url=f"{settings.api_v1_prefix}/trips/tasks/{task.id}",
    )


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatus,
    summary="Poll a queued planning run",
)
async def task_status(task_id: str, user: CurrentUser) -> TaskStatus:
    from celery.result import AsyncResult

    from app.workers.celery_app import celery_app

    try:
        result = AsyncResult(task_id, app=celery_app)
        state = result.state
        ready = result.ready()
    except Exception as exc:
        raise ExternalServiceError(
            "Could not reach the Celery result backend.",
            details={"reason": str(exc)[:200]},
        ) from exc

    payload = TaskStatus(task_id=task_id, state=state, ready=ready)
    if ready:
        if result.successful():
            value = result.result
            payload.result = value if isinstance(value, dict) else {"value": str(value)}
        else:
            payload.error = str(result.result)[:500]
    return payload


@router.get(
    "/{trip_id}/itinerary",
    response_model=ItineraryPublic,
    summary="Get the trip's latest itinerary",
)
async def latest_itinerary(trip_id: str, user: CurrentUser) -> ItineraryPublic:
    trip = await trip_service.get_owned_trip(trip_id, user)
    return ItineraryPublic.model_validate(await itinerary_service.latest_for_trip(trip))


@router.get(
    "/{trip_id}/itineraries",
    response_model=list[ItinerarySummary],
    summary="List every itinerary version for a trip",
)
async def list_itineraries(trip_id: str, user: CurrentUser) -> list[ItinerarySummary]:
    trip = await trip_service.get_owned_trip(trip_id, user)
    itineraries = await itinerary_service.list_for_trip(trip)
    return [
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
        for i in itineraries
    ]


@router.post(
    "/{trip_id}/share",
    response_model=TripPublic,
    summary="Publish a trip behind a share token",
)
async def share(trip_id: str, user: CurrentUser) -> TripPublic:
    trip = await trip_service.get_owned_trip(trip_id, user)
    return _to_public(await trip_service.enable_sharing(trip))


@router.delete(
    "/{trip_id}/share",
    response_model=TripPublic,
    summary="Unpublish a trip and invalidate its link",
)
async def unshare(trip_id: str, user: CurrentUser) -> TripPublic:
    trip = await trip_service.get_owned_trip(trip_id, user)
    return _to_public(await trip_service.disable_sharing(trip))


@router.get(
    "/shared/{share_token}",
    response_model=ItineraryPublic,
    tags=["public"],
    summary="Read a shared itinerary without signing in",
)
async def read_shared(share_token: str) -> ItineraryPublic:
    trip = await trip_service.get_shared_trip(share_token)
    return ItineraryPublic.model_validate(await itinerary_service.latest_for_trip(trip))


def _to_public(trip) -> TripPublic:
    """Project a Trip document, adding the computed duration."""
    return TripPublic(
        id=trip.id,
        user_id=trip.user_id,
        title=trip.title,
        destination=trip.destination,
        start_date=trip.start_date,
        end_date=trip.end_date,
        duration_days=trip.duration_days,
        party=trip.party,
        constraints=trip.constraints,
        status=trip.status,
        active_itinerary_id=trip.active_itinerary_id,
        is_public=trip.is_public,
        share_token=trip.share_token,
        created_at=trip.created_at,
        updated_at=trip.updated_at,
    )
