"""Itinerary generation, versioning and manual editing."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from beanie import PydanticObjectId

from app.ai.graph import run_planner
from app.ai.pipeline import build_itinerary_document
from app.core.config import OptimizerAlgorithm
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    OptimizationError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.timeutils import MINUTES_PER_DAY, overflows_day, to_clock, to_minutes
from app.db.sorting import descending
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
from app.optimization.distance import Coordinate, TravelMatrix, leg_figures
from app.optimization.engine import compare_algorithms, optimize
from app.optimization.schemas import (
    DayConfig,
    OptimizationPlace,
    OptimizationRequest,
    OptimizationResult,
)
from app.schemas.itinerary import (
    AlgorithmInfo,
    AlgorithmRun,
    ApplyRouteRequest,
    CompareRouteRequest,
    CompareRouteResponse,
    OptimizedStop,
    OptimizeRouteRequest,
    OptimizeRouteResponse,
    ReorderDayRequest,
)
from app.services import routing_service

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
        .sort(descending(Itinerary.version))
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
        .sort(descending(Itinerary.version))
        .to_list()
    )


async def latest_for_trip(trip: Trip) -> Itinerary:
    """Return the newest itinerary for a trip.

    Raises:
        NotFoundError: when the trip has never been planned.
    """
    itineraries = (
        await Itinerary.find(Itinerary.trip_id == trip.id)
        .sort(descending(Itinerary.version))
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

    # One matrix for the day, over its located activities in the new order;
    # node ``k`` is the ``k``-th of them. Legs are priced exactly as the
    # optimiser prices them, so applying a compared route yields the figures
    # the comparison showed.
    located = [a for a in reordered if a.location is not None]
    node_of = {id(a): k for k, a in enumerate(located)}
    matrix = await routing_service.travel_matrix(
        [Coordinate(*a.location.as_lat_lng()) for a in located if a.location is not None],
        TransportMode.WALKING,
    )

    # Flow the whole day first, so an overrun is rejected before anything is
    # mutated and the stored itinerary is never left half-rewritten.
    clock = day_start
    previous_node: int | None = None
    planned: list[tuple[int, int, TravelLeg | None]] = []

    for activity in reordered:
        leg: TravelLeg | None = None
        node = node_of.get(id(activity))
        if previous_node is not None and node is not None:
            distance, minutes, mode = leg_figures(
                matrix, previous_node, node, TransportMode.WALKING
            )
            leg = TravelLeg(
                mode=mode,
                distance_km=round(distance, 3),
                duration_minutes=minutes,
                estimated=matrix.estimated,
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
        if node is not None:
            previous_node = node

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


def _to_milliseconds(value: datetime) -> datetime:
    """Normalise to UTC at millisecond precision, MongoDB's own resolution.

    Timestamps read back from the database are naive UTC; one parsed from a JSON
    body may carry an offset. Both are brought to the same footing before any
    comparison.
    """
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


async def apply_route_to_day(
    itinerary: Itinerary, day_number: int, payload: ApplyRouteRequest
) -> Itinerary:
    """Apply a compared route's ordering to one day.

    Everything the client sends is treated as a claim to be checked:

    * **Staleness.** ``expected_updated_at`` must match the stored itinerary. A
      comparison made before someone else edited the day describes a day that no
      longer exists, so applying it would silently discard their edit.
    * **Membership.** The place ids must be exactly the day's routable places -
      no foreign ids, none missing, none repeated. Anything else is a payload
      that was not produced by comparing *this* day.
    * **Figures.** None are accepted. Times, travel legs and totals are
      recomputed from the stored places by the same code path a manual reorder
      uses, so an applied route cannot disagree with itself.

    Activities with no ``place_id`` - a meal dropped into a gap, a block of free
    time - are not part of the comparison and keep their position in the
    sequence, so applying a route cannot shuffle lunch to the end of the day.

    Raises:
        NotFoundError: when the day is not part of this itinerary.
        ConflictError: when the itinerary changed after the comparison was made.
        ValidationError: when the ids are not exactly the day's routable places.
        OptimizationError: when the order cannot fit inside the day.
    """
    day = next((d for d in itinerary.days if d.day_number == day_number), None)
    if day is None:
        raise NotFoundError(f"Day {day_number} is not part of this itinerary.")

    # Compared at millisecond resolution, which is what MongoDB actually stores.
    # Neither looser nor tighter works: a response serialised straight from the
    # in-memory document carries microseconds that the database never kept, so an
    # exact match can never hold; truncating to whole seconds would miss two
    # edits inside the same second, which is precisely when a stale apply is most
    # likely to happen.
    stored = _to_milliseconds(itinerary.updated_at)
    expected = _to_milliseconds(payload.expected_updated_at)
    if stored != expected:
        raise ConflictError(
            "This day has changed since the routes were compared. Re-run the "
            "comparison so you are choosing between the current options.",
            details={
                "expected_updated_at": expected.isoformat(),
                "current_updated_at": stored.isoformat(),
            },
        )

    requested = [str(place_id) for place_id in payload.place_ids]
    if len(set(requested)) != len(requested):
        raise ValidationError(
            "The same place appears more than once in the requested order.",
            details={"place_ids": requested},
        )

    # Index by position so the routable slots can be refilled in place.
    routable = {
        str(activity.place_id): index
        for index, activity in enumerate(day.activities)
        if activity.place_id is not None
    }

    unknown = [place_id for place_id in requested if place_id not in routable]
    if unknown:
        raise ValidationError(
            "Those places are not on this day.",
            details={"unknown_place_ids": unknown},
        )

    missing = [place_id for place_id in routable if place_id not in set(requested)]
    if missing:
        raise ValidationError(
            "The requested order leaves out places that are on this day. Applying "
            "a route reorders the day; it does not remove anything from it.",
            details={"missing_place_ids": missing},
        )

    # Non-routable activities hold their slots; the routable ones are dealt into
    # the slots that remain, in the requested order.
    fixed_slots = {
        index for index, activity in enumerate(day.activities) if activity.place_id is None
    }
    reordered_indices = iter(routable[place_id] for place_id in requested)
    activity_order = [
        index if index in fixed_slots else next(reordered_indices)
        for index in range(len(day.activities))
    ]

    updated = await reorder_day(
        itinerary,
        ReorderDayRequest(day_number=day_number, activity_order=activity_order),
    )
    # reorder_day marks the day as hand-edited; this one was not, so name the
    # algorithm that produced it instead.
    updated.optimization.solver_status = f"applied:{payload.algorithm.value}"
    await updated.save()

    logger.info(
        "route_applied",
        itinerary_id=str(updated.id),
        day_number=day_number,
        algorithm=payload.algorithm.value,
        places=len(requested),
    )
    return updated


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
    request.matrix = await _route_matrix(request)

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
        estimated=request.matrix.estimated,
        routing_provider=request.matrix.provider,
    )


async def compare_route_algorithms(payload: CompareRouteRequest) -> CompareRouteResponse:
    """Run several algorithms on the same places so their plans can be compared.

    Raises:
        NotFoundError: when fewer than two of the supplied ids resolve.
        OptimizationError: when no algorithm finds a feasible plan.
    """
    ordered = await _load_ordered_places(payload.place_ids)
    request = _build_route_request(payload, ordered)
    # Fetched once, before any algorithm runs: every run is priced on it.
    matrix = request.matrix = await _route_matrix(request)

    results = await asyncio.to_thread(compare_algorithms, request, payload.algorithms)
    feasible = [r for r in results if r.feasible]
    if not feasible:
        raise OptimizationError(
            "No feasible route exists for those places within the given hours."
        )

    best = min(feasible, key=_comparable_cost)
    fastest = min(results, key=lambda r: r.duration_seconds)
    best_cost = _comparable_cost(best)

    runs: list[AlgorithmRun] = []
    for result in results:
        info = _algorithm_info(result.engine)
        gap = (_comparable_cost(result) - best_cost) / best_cost * 100 if best_cost else 0.0
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
        runs=runs,
        best_algorithm=best.engine,
        fastest_algorithm=fastest.engine,
        estimated=matrix.estimated,
        routing_provider=matrix.provider,
    )


async def _route_matrix(request: OptimizationRequest) -> TravelMatrix:
    """The travel matrix for a request: origin first, then each place."""
    coords = [request.origin] + [p.coordinate for p in request.places]
    return await routing_service.travel_matrix(coords, request.mode)


def _comparable_cost(result: OptimizationResult) -> int:
    """The score ``compare_algorithms`` attached to a result.

    ``objective_value`` is optional on the dataclass because a plan can exist
    before anyone has costed it, but every result from ``compare_algorithms`` has
    been through the shared evaluator. Stating that here keeps the comparison and
    the gap arithmetic on plain integers, and turns a broken contract into a
    reported failure rather than a ``TypeError`` halfway through building a
    response.
    """
    if result.objective_value is None:  # pragma: no cover - contract of the comparer
        raise OptimizationError(
            f"The {result.engine} run returned a plan with no comparable cost."
        )
    return result.objective_value


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
