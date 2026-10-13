"""Trip CRUD, ownership checks and sharing."""

from __future__ import annotations

import secrets
from typing import Any

from beanie import PydanticObjectId

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.db.sorting import descending
from app.models.enums import TripStatus
from app.models.itinerary import Itinerary
from app.models.trip import Trip
from app.models.user import User
from app.observability.logging import get_logger
from app.schemas.trip import TripCreate, TripUpdate

logger = get_logger(__name__)


async def create_trip(user: User, payload: TripCreate) -> Trip:
    """Create a trip, seeding unset constraints from the user's saved preferences.

    A traveller who has already told us they use a wheelchair should not have to
    repeat that on every trip, so the profile fills the gaps the request leaves.
    """
    constraints = payload.constraints
    preferences = user.preferences

    if not constraints.interests:
        constraints.interests = list(preferences.interests)
    if not constraints.accessibility_needs:
        constraints.accessibility_needs = list(preferences.accessibility_needs)
    if not constraints.dietary_restrictions:
        constraints.dietary_restrictions = list(preferences.dietary_restrictions)

    destination = payload.destination.to_destination()
    trip = Trip(
        user_id=user.id,  # type: ignore[arg-type]
        title=payload.title or f"Trip to {destination.city}",
        destination=destination,
        start_date=payload.start_date,
        end_date=payload.end_date,
        party=payload.party,
        constraints=constraints,
        status=TripStatus.DRAFT,
    )
    await trip.insert()
    logger.info("trip_created", trip_id=str(trip.id), user_id=str(user.id))
    return trip


async def get_trip(trip_id: str | PydanticObjectId) -> Trip:
    """Fetch a trip.

    Raises:
        NotFoundError: when the id is malformed or no such trip exists.
    """
    try:
        oid = PydanticObjectId(str(trip_id))
    except Exception as exc:
        raise NotFoundError("Trip not found.") from exc

    trip = await Trip.get(oid)
    if trip is None:
        raise NotFoundError("Trip not found.")
    return trip


async def get_owned_trip(trip_id: str | PydanticObjectId, user: User) -> Trip:
    """Fetch a trip the caller owns.

    Raises:
        NotFoundError: when the trip does not exist.
        PermissionDeniedError: when it belongs to someone else.
    """
    trip = await get_trip(trip_id)
    if trip.user_id != user.id and user.role != "admin":
        raise PermissionDeniedError("This trip belongs to another account.")
    return trip


async def list_trips(
    user: User,
    *,
    status: TripStatus | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[Trip], int]:
    query: dict[str, Any] = {"user_id": user.id}
    if status:
        query["status"] = status

    total = await Trip.find(query).count()
    items = (
        await Trip.find(query)
        .sort(descending(Trip.created_at))
        .skip(skip)
        .limit(limit)
        .to_list()
    )
    return items, total


async def update_trip(trip: Trip, payload: TripUpdate) -> Trip:
    """Apply a partial update.

    Changing dates or constraints invalidates any existing itinerary, so the trip
    drops back to DRAFT rather than presenting a stale plan as current.
    """
    data = payload.model_dump(exclude_unset=True)
    destination = data.pop("destination", None)
    if destination is not None:
        trip.destination = payload.destination.to_destination()  # type: ignore[union-attr]

    planning_fields = {"start_date", "end_date", "constraints", "party", "destination"}
    changed = set(data) | ({"destination"} if destination else set())
    invalidates = bool(planning_fields & changed)

    for field, value in data.items():
        setattr(trip, field, value)

    # Re-run the document validators, so an update cannot produce a trip the
    # constructor would have rejected (end before start, over the day limit).
    Trip.model_validate(trip.model_dump())

    if invalidates and trip.status is not TripStatus.DRAFT:
        trip.status = TripStatus.DRAFT
        trip.active_itinerary_id = None

    await trip.save()
    return trip


async def delete_trip(trip: Trip) -> None:
    """Delete a trip along with every itinerary generated for it."""
    await Itinerary.find(Itinerary.trip_id == trip.id).delete()
    await trip.delete()
    logger.info("trip_deleted", trip_id=str(trip.id))


async def enable_sharing(trip: Trip) -> Trip:
    """Publish a trip behind an unguessable token."""
    if not trip.share_token:
        trip.share_token = secrets.token_urlsafe(24)
    trip.is_public = True
    await trip.save()
    return trip


async def disable_sharing(trip: Trip) -> Trip:
    """Unpublish a trip and burn its token, so old links stop working."""
    trip.is_public = False
    trip.share_token = None
    await trip.save()
    return trip


async def get_shared_trip(share_token: str) -> Trip:
    """Look up a publicly shared trip by token.

    Raises:
        NotFoundError: when the token is unknown or sharing has been disabled.
    """
    trip = await Trip.find_one(Trip.share_token == share_token)
    if trip is None or not trip.is_public:
        raise NotFoundError("This shared trip is unavailable.")
    return trip


async def trip_stats(user: User) -> dict[str, Any]:
    """Headline counts for the user's dashboard."""
    from app.db.mongodb import get_database

    database = get_database()
    rows = await (
        database["trips"]
        .aggregate(
            [
                {"$match": {"user_id": user.id}},
                {"$group": {"_id": "$status", "count": {"$sum": 1}}},
            ]
        )
        .to_list(length=None)
    )
    by_status = {row["_id"]: row["count"] for row in rows}
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "itineraries": await Itinerary.find(Itinerary.user_id == user.id).count(),
    }
