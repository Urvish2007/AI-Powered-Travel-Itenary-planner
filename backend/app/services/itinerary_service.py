"""Itinerary generation, versioning and manual editing."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any

from beanie import PydanticObjectId

from app.ai.graph import run_planner
from app.ai.pipeline import build_itinerary_document
from app.core.config import OptimizerAlgorithm
from app.core.exceptions import NotFoundError, OptimizationError, PermissionDeniedError
from app.core.timeutils import MINUTES_PER_DAY, overflows_day, to_clock, to_minutes
from app.models.ai_run import AIRun
from app.models.enums import (
    AIRunStatus,
    TransportMode,
    TripStatus,
)
from app.models.itinerary import Itinerary, TravelLeg
from app.models.place import Place
from app.models.trip import Trip
from app.models.user import User
from app.observability.logging import get_logger
from app.optimization.algorithms import ALGORITHM_INFO
from app.optimization.distance import (
    Coordinate,
    effective_mode,
    haversine_km,
    travel_minutes,
)
from app.optimization.engine import compare_algorithms, optimize
from app.optimization.schemas import (
    DayConfig,
    OptimizationPlace,
    OptimizationRequest,
)
from app.schemas.itinerary import (
    AlgorithmInfo,
    AlgorithmRun,
    CompareRouteRequest,
    CompareRouteResponse,
    OptimizedStop,
    OptimizeRouteRequest,
    OptimizeRouteResponse,
    ReorderDayRequest,
)

logger = get_logger(__name__)


async def generate_itinerary(
    trip: Trip, *, brief: str = "", regenerate: bool = False
) -> Itinerary:
    """Run the planning pipeline and persist the resulting itinerary.

    Each run produces a new version rather than overwriting the last, so a
    regeneration the traveller dislikes can be compared against - and rolled back
    to - the previous plan.

    Raises:
        OptimizationError: when no feasible schedule exists.
    """
    started = time.perf_counter()

    existing = (
        await Itinerary.find(Itinerary.trip_id == trip.id)
        .sort(-Itinerary.version)
        .limit(1)
        .to_list()
    )
    if existing and not regenerate:
        return existing[0]

    version = (existing[0].version + 1) if existing else 1

    run = AIRun(
        trip_id=trip.id,
        user_id=trip.user_id,
        kind="generate_itinerary",
        status=AIRunStatus.STARTED,
    )
    await run.insert()

    trip.status = TripStatus.PLANNING
    await trip.save()

    try:
        state = await run_planner(trip, brief)
        itinerary = build_itinerary_document(state, version=version)
        await itinerary.insert()
    except OptimizationError as exc:
        run.status = AIRunStatus.FAILED
        run.error = exc.message
        run.latency_ms = int((time.perf_counter() - started) * 1000)
        await run.save()

        trip.status = TripStatus.DRAFT
        await trip.save()
        logger.warning(
            "itinerary_generation_failed",
            trip_id=str(trip.id),
            error=exc.message,
        )
        raise
    except Exception as exc:
        run.status = AIRunStatus.FAILED
        run.error = str(exc)
        await run.save()
        trip.status = TripStatus.DRAFT
        await trip.save()
        raise

    trip.status = TripStatus.READY
    trip.active_itinerary_id = itinerary.id
    await trip.save()

    run.status = AIRunStatus.SUCCEEDED
    run.itinerary_id = itinerary.id
    run.provider = state.provider
    run.model = state.model
    run.prompt_tokens = state.prompt_tokens
    run.completion_tokens = state.completion_tokens
    run.latency_ms = int((time.perf_counter() - started) * 1000)
    run.steps = state.steps
    await run.save()

    logger.info(
        "itinerary_generated",
        trip_id=str(trip.id),
        itinerary_id=str(itinerary.id),
        version=version,
        days=len(itinerary.days),
        distance_km=itinerary.total_distance_km,
    )
    return itinerary


async def get_itinerary(itinerary_id: str | PydanticObjectId) -> Itinerary:
    """Fetch one itinerary.

    Raises:
        NotFoundError: when the id is malformed or no such itinerary exists.
    """
    try:
        oid = PydanticObjectId(str(itinerary_id))
    except Exception as exc:
        raise NotFoundError("Itinerary not found.") from exc

    itinerary = await Itinerary.get(oid)
    if itinerary is None:
        raise NotFoundError("Itinerary not found.")
    return itinerary


async def get_owned_itinerary(
    itinerary_id: str | PydanticObjectId, user: User
) -> Itinerary:
    itinerary = await get_itinerary(itinerary_id)
    if itinerary.user_id != user.id and user.role != "admin":
        raise PermissionDeniedError("This itinerary belongs to another account.")
    return itinerary


async def list_for_trip(trip: Trip) -> list[Itinerary]:
    return (
        await Itinerary.find(Itinerary.trip_id == trip.id)
        .sort(-Itinerary.version)
        .to_list()
    )


async def latest_for_trip(trip: Trip) -> Itinerary:
    """Return the newest itinerary for a trip.

    Raises:
        NotFoundError: when the trip has never been planned.
    """
    itineraries = (
        await Itinerary.find(Itinerary.trip_id == trip.id)
        .sort(-Itinerary.version)
        .limit(1)
        .to_list()
    )
    if not itineraries:
        raise NotFoundError("This trip has no itinerary yet.")
    return itineraries[0]


async def delete_itinerary(itinerary: Itinerary) -> None:
    """Delete an itinerary, clearing the trip's pointer when it was the active one."""
    trip = await Trip.get(itinerary.trip_id)
    if trip is not None and trip.active_itinerary_id == itinerary.id:
        trip.active_itinerary_id = None
        trip.status = TripStatus.DRAFT
        await trip.save()
    await itinerary.delete()


async def set_active(trip: Trip, itinerary: Itinerary) -> Trip:
    """Point a trip at a specific itinerary version."""
    if itinerary.trip_id != trip.id:
        raise PermissionDeniedError("That itinerary belongs to a different trip.")
    trip.active_itinerary_id = itinerary.id
    trip.status = TripStatus.READY
    await trip.save()
    return trip


async def reorder_day(itinerary: Itinerary, payload: ReorderDayRequest) -> Itinerary:
    """Reorder one day's activities and recompute every derived time.

    The client sends the order it wants; arrival and departure times, travel legs
    and totals are all recalculated here, so the stored plan can never drift out
    of step with itself.

    Raises:
        NotFoundError: when the day does not exist.
        OptimizationError: when the supplied order is not a permutation of the
            day's activities, or when the requested order cannot fit inside a
            single day once travel time between the new neighbours is counted.
    """
    day = next((d for d in itinerary.days if d.day_number == payload.day_number), None)
    if day is None:
        raise NotFoundError(f"Day {payload.day_number} is not part of this itinerary.")

    if sorted(payload.activity_order) != list(range(len(day.activities))):
        raise OptimizationError(
            "activity_order must be a permutation of the day's activity indices.",
            details={
                "expected_indices": list(range(len(day.activities))),
                "received": payload.activity_order,
            },
        )

    # The day keeps the start time it already had. Anchoring on the *new* first
    # activity instead would drag the whole day to wherever that activity
    # happened to sit, which is not what reordering means.
    day_start = min(to_minutes(a.start_time) for a in day.activities)

    reordered = [day.activities[i] for i in payload.activity_order]

    # Flow the whole day first, so an overrun is rejected before anything is
    # mutated and the stored itinerary is never left half-rewritten.
    clock = day_start
    previous_location = None
    planned: list[tuple[int, int, TravelLeg | None]] = []

    for activity in reordered:
        leg: TravelLeg | None = None
        if previous_location is not None and activity.location is not None:
            distance = haversine_km(
                Coordinate(*previous_location.as_lat_lng()),
                Coordinate(*activity.location.as_lat_lng()),
            )
            mode = effective_mode(distance, TransportMode.WALKING)
            minutes = travel_minutes(distance, mode)
            leg = TravelLeg(
                mode=mode, distance_km=round(distance, 3), duration_minutes=minutes
            )
            clock += minutes

        start, end = clock, clock + activity.duration_minutes
        if overflows_day(end):
            raise OptimizationError(
                "That order does not fit inside a single day: "
                f"{activity.title!r} would finish after midnight. Remove an "
                "activity or choose a different order.",
                details={
                    "day_number": payload.day_number,
                    "activity": activity.title,
                    "day_start_time": to_clock(day_start),
                    "overrun_minutes": end - (MINUTES_PER_DAY - 1),
                },
            )

        planned.append((start, end, leg))
        clock = end
        if activity.location is not None:
            previous_location = activity.location

    for order, (activity, (start, end, leg)) in enumerate(
        zip(reordered, planned, strict=True)
    ):
        activity.travel_from_previous = leg
        activity.start_time = to_clock(start)
        activity.end_time = to_clock(end)
        activity.order = order

    day.activities = reordered
    itinerary.recalculate_totals()
    # The schedule is now hand-edited, so the solver report no longer describes it.
    itinerary.optimization.solver_status = "manually_reordered"
    await itinerary.save()
    return itinerary


async def remove_activity(
    itinerary: Itinerary, day_number: int, activity_index: int
) -> Itinerary:
    """Drop one activity and re-flow the remaining ones."""
    day = next((d for d in itinerary.days if d.day_number == day_number), None)
    if day is None:
        raise NotFoundError(f"Day {day_number} is not part of this itinerary.")
    if not 0 <= activity_index < len(day.activities):
        raise NotFoundError(f"Day {day_number} has no activity at index {activity_index}.")

    day.activities.pop(activity_index)
    if day.activities:
        return await reorder_day(
            itinerary,
            ReorderDayRequest(
                day_number=day_number,
                activity_order=list(range(len(day.activities))),
            ),
        )

    itinerary.recalculate_totals()
    await itinerary.save()
    return itinerary


async def _load_ordered_places(place_ids: list[PydanticObjectId]) -> list[Place]:
    """Fetch the places in the caller's order, so the 'before' distance is meaningful."""
    places = await Place.find({"_id": {"$in": place_ids}}).to_list()
    if len(places) < 2:
        raise NotFoundError("At least two of the supplied place ids must exist.")
    by_id = {str(p.id): p for p in places}
    return [by_id[str(pid)] for pid in place_ids if str(pid) in by_id]


def _build_route_request(
    payload: OptimizeRouteRequest | CompareRouteRequest, ordered: list[Place]
) -> OptimizationRequest:
    try:
        mode = TransportMode(payload.transport_mode)
    except ValueError:
        mode = TransportMode.WALKING

    day_start = to_minutes(payload.day_start_time)
    day_end = to_minutes(payload.day_end_time)
    if day_start >= day_end:
        raise OptimizationError("day_start_time must be earlier than day_end_time.")

    from datetime import date as date_type

    # Opening hours depend on the weekday, so plan against the day the caller means.
    today = payload.start_date or date_type.today()
    return OptimizationRequest(
        places=[
            OptimizationPlace(
                ref=str(place.id),
                name=place.name,
                coordinate=Coordinate(*place.location.as_lat_lng()),
                visit_minutes=place.average_visit_minutes,
                score=0.5 + 0.1 * place.rating,
                cost=place.cost.amount,
                opening=_opening_windows(place),
                always_open=not place.opening_hours,
                category=str(place.category),
            )
            for place in ordered
        ],
        days=[
            DayConfig(
                index=offset,
                day_date=today + timedelta(days=offset),
                weekday=(today + timedelta(days=offset)).weekday(),
                start_minutes=day_start,
                end_minutes=day_end,
            )
            for offset in range(payload.days)
        ],
        origin=Coordinate(payload.start_latitude, payload.start_longitude),
        mode=mode,
        max_places_per_day=payload.max_places_per_day,
        max_daily_distance_km=200.0,
        time_limit_seconds=payload.time_limit_seconds,
        algorithm=getattr(payload, "algorithm", None),
    )


def _to_optimized_stops(result: Any, ordered: list[Place]) -> list[OptimizedStop]:
    stops: list[OptimizedStop] = []
    for day in result.days:
        for order, stop in enumerate(day.stops):
            place = ordered[stop.place_index]
            stops.append(
                OptimizedStop(
                    place_id=place.id,  # type: ignore[arg-type]
                    name=place.name,
                    day_number=day.day_index + 1,
                    order=order,
                    arrival_time=to_clock(stop.arrival_minutes),
                    departure_time=to_clock(stop.departure_minutes),
                    travel_km=stop.travel_km,
                    travel_minutes=stop.travel_minutes,
                    mode=str(stop.mode),
                )
            )
    return stops


async def optimize_places(
    payload: OptimizeRouteRequest,
) -> OptimizeRouteResponse:
    """Optimise an arbitrary set of places, with no trip involved.

    Raises:
        NotFoundError: when fewer than two of the supplied ids resolve.
    """
    ordered = await _load_ordered_places(payload.place_ids)
    request = _build_route_request(payload, ordered)

    # The solvers are CPU-bound; a worker thread keeps the event loop responsive.
    result = await asyncio.to_thread(optimize, request)
    if not result.feasible:
        raise OptimizationError(
            "No feasible route exists for those places within the given hours.",
            details={"solver_status": result.status},
        )

    return OptimizeRouteResponse(
        stops=_to_optimized_stops(result, ordered),
        dropped_place_ids=[ordered[i].id for i in result.dropped],  # type: ignore[misc]
        total_distance_km=result.total_distance_km,
        distance_before_km=result.distance_before_km,
        solver_status=result.status,
        engine=result.engine,
        duration_seconds=result.duration_seconds,
        objective=result.objective_value,
    )


async def compare_route_algorithms(payload: CompareRouteRequest) -> CompareRouteResponse:
    """Run several algorithms on the same places so their plans can be compared.

    Raises:
        NotFoundError: when fewer than two of the supplied ids resolve.
        OptimizationError: when no algorithm finds a feasible plan.
    """
    ordered = await _load_ordered_places(payload.place_ids)
    request = _build_route_request(payload, ordered)

    results = await asyncio.to_thread(compare_algorithms, request, payload.algorithms)
    feasible = [r for r in results if r.feasible]
    if not feasible:
        raise OptimizationError(
            "No feasible route exists for those places within the given hours."
        )

    best = min(feasible, key=lambda r: r.objective_value)
    fastest = min(results, key=lambda r: r.duration_seconds)

    runs: list[AlgorithmRun] = []
    for result in results:
        info = _algorithm_info(result.engine)
        gap = (
            (result.objective_value - best.objective_value) / best.objective_value * 100
            if best.objective_value
            else 0.0
        )
        runs.append(
            AlgorithmRun(
                algorithm=result.engine,
                label=info.get("label", result.engine),
                solver_status=result.status,
                scheduled_count=result.scheduled_count,
                dropped_count=len(result.dropped),
                total_distance_km=result.total_distance_km,
                travel_minutes=sum(d.total_travel_minutes for d in result.days),
                objective=result.objective_value,
                gap_percent=round(gap, 2),
                duration_seconds=result.duration_seconds,
                is_best=result is best,
                stops=_to_optimized_stops(result, ordered),
                dropped_place_ids=[ordered[i].id for i in result.dropped],  # type: ignore[misc]
            )
        )

    return CompareRouteResponse(
        runs=runs, best_algorithm=best.engine, fastest_algorithm=fastest.engine
    )


def _algorithm_info(engine: str) -> dict[str, str]:
    """Metadata for an engine name; the greedy fallback is not a selectable algorithm."""
    try:
        return ALGORITHM_INFO[OptimizerAlgorithm(engine)]
    except (ValueError, KeyError):
        return {"label": engine}


def list_optimizer_algorithms() -> list[AlgorithmInfo]:
    return [AlgorithmInfo(key=a.value, **info) for a, info in ALGORITHM_INFO.items()]


def _opening_windows(place: Place) -> dict[int, tuple[int, int]]:
    windows: dict[int, tuple[int, int]] = {}
    for window in place.opening_hours:
        if window.closed or not window.opens or not window.closes:
            continue
        opens, closes = to_minutes(window.opens), to_minutes(window.closes)
        if closes <= opens:
            closes = to_minutes("23:59")
        windows[window.day_of_week] = (opens, closes)
    return windows


async def itinerary_stats(itinerary: Itinerary) -> dict[str, Any]:
    """Derived figures the UI shows without needing to recompute them client-side."""
    activity_count = sum(len(day.activities) for day in itinerary.days)
    categories: dict[str, int] = {}
    for day in itinerary.days:
        for activity in day.activities:
            key = str(activity.kind)
            categories[key] = categories.get(key, 0) + 1

    busiest = max(itinerary.days, key=lambda d: len(d.activities), default=None)
    return {
        "day_count": len(itinerary.days),
        "activity_count": activity_count,
        "activities_per_day": (
            round(activity_count / len(itinerary.days), 2) if itinerary.days else 0
        ),
        "by_kind": categories,
        "total_distance_km": itinerary.total_distance_km,
        "total_estimated_cost": itinerary.total_estimated_cost,
        "currency": itinerary.currency,
        "busiest_day": busiest.day_number if busiest else None,
        "optimization": itinerary.optimization.model_dump(),
    }
