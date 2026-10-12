"""Itinerary optimisation with Google OR-Tools.

The problem is modelled as a capacitated vehicle-routing problem with time
windows, where **one vehicle represents one day of the trip**. All days share a
single global timeline - day ``d`` owns minutes ``[d * 1440, (d + 1) * 1440)`` -
which lets a single solve decide both *which day* each place lands on and *what
order* it is visited in. Solving those two questions together is what makes the
result meaningfully better than clustering places by day and routing each day
separately.

Constraints expressed to the solver:

* **Opening hours** - per-node time windows, with the closed periods punched out
  of the node's domain so a place is never scheduled on a day it is shut.
* **Day boundaries** - the overnight gap between one day's end and the next
  day's start is removed from every node's domain.
* **Visit duration** - service time, folded into the transit callback.
* **Daily pace** - a counting dimension caps places per day.
* **Daily distance** - a distance dimension caps how far a day may range.
* **Optional visits** - disjunctions with a score-weighted drop penalty, so the
  solver sheds the least interesting places when time runs out rather than
  failing outright. Places the user marked mandatory get no disjunction.

If OR-Tools cannot be imported, or the solver returns no assignment, the module
falls back to a greedy nearest-neighbour construction with 2-opt improvement so
the API always returns a usable plan.
"""

from __future__ import annotations

import time
from itertools import pairwise
from typing import Any

from app.core.config import OptimizerAlgorithm, settings
from app.observability.logging import get_logger
from app.observability.metrics import OPTIMIZER_DURATION, OPTIMIZER_RUNS
from app.optimization import algorithms
from app.optimization.distance import (
    Coordinate,
    build_travel_matrix,
    effective_mode,
    haversine_km,
)
from app.optimization.schemas import (
    MINUTES_PER_DAY,
    DaySchedule,
    OptimizationRequest,
    OptimizationResult,
    ScheduledStop,
)

logger = get_logger(__name__)

try:  # pragma: no cover - import guard
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    ORTOOLS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without ortools
    pywrapcp = None  # type: ignore[assignment]
    routing_enums_pb2 = None  # type: ignore[assignment]
    ORTOOLS_AVAILABLE = False

# Drop penalties are expressed in the same unit as arc costs (minutes). A base of
# 600 means the solver will happily spend up to ten hours of travel to keep a
# top-scoring place in the plan, which in practice means "never drop it unless
# the day physically cannot fit it".
DROP_PENALTY_BASE = 600
DROP_PENALTY_SCORE_WEIGHT = 1200
MANDATORY_UNSERVED_PENALTY = 10_000_000


def resolve_algorithm(
    request: OptimizationRequest, algorithm: OptimizerAlgorithm | str | None = None
) -> OptimizerAlgorithm:
    """Pick the algorithm: an explicit argument, else the request, else settings."""
    return OptimizerAlgorithm(
        algorithm or request.algorithm or settings.optimizer_algorithm
    )


def optimize(
    request: OptimizationRequest, algorithm: OptimizerAlgorithm | str | None = None
) -> OptimizationResult:
    """Optimise a multi-day itinerary.

    Always returns a result: on solver failure it degrades to the greedy engine
    rather than raising, because a slightly worse plan beats no plan.

    Args:
        request: The itinerary to plan.
        algorithm: Which algorithm to run. Defaults to ``request.algorithm`` and then
            ``settings.optimizer_algorithm`` (OR-Tools unless configured otherwise).
    """
    if not request.places or not request.days:
        return OptimizationResult(
            days=[DaySchedule(day_index=d.index) for d in request.days],
            status="empty_input",
            engine="none",
        )

    baseline = _baseline_distance_km(request)
    chosen = resolve_algorithm(request, algorithm)

    if chosen is not OptimizerAlgorithm.ORTOOLS:
        try:
            if chosen is OptimizerAlgorithm.AUTO:
                result = algorithms.solve_portfolio(
                    request, _solve_with_ortools if ORTOOLS_AVAILABLE else None
                )
            else:
                result = algorithms.solve(request, chosen)
            if result.feasible:
                result.distance_before_km = baseline
                OPTIMIZER_RUNS.labels("success").inc()
                return result
            logger.warning("optimizer_no_solution", algorithm=chosen.value)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("optimizer_crashed", algorithm=chosen.value, error=str(exc))
            OPTIMIZER_RUNS.labels("error").inc()
        # Fall through: a plan from the long-standing solver beats no plan.

    if ORTOOLS_AVAILABLE:
        try:
            # Its own name: OR-Tools may return nothing, unlike the greedy
            # fallback below, which always produces a plan.
            ortools_result = _solve_with_ortools(request)
            if ortools_result is not None and ortools_result.feasible:
                ortools_result.distance_before_km = baseline
                OPTIMIZER_RUNS.labels("success").inc()
                return ortools_result
            logger.warning(
                "optimizer_no_solution",
                places=len(request.places),
                days=len(request.days),
                falling_back_to="greedy",
            )
        except Exception as exc:  # pragma: no cover - solver internals
            logger.exception("optimizer_crashed", error=str(exc))
            OPTIMIZER_RUNS.labels("error").inc()
    else:
        logger.warning("ortools_unavailable", falling_back_to="greedy")

    result = _solve_greedy(request)
    result.distance_before_km = baseline
    OPTIMIZER_RUNS.labels("fallback").inc()
    return result


def compare_algorithms(
    request: OptimizationRequest,
    selected: list[OptimizerAlgorithm] | None = None,
) -> list[OptimizationResult]:
    """Run several algorithms on the same request and return every result.

    Each result's ``objective_value`` is computed by the shared evaluator, so the
    numbers are directly comparable (lower is better). ``AUTO`` is not offered here:
    it is itself a comparison.
    """
    wanted = selected or [
        OptimizerAlgorithm.NEAREST_NEIGHBOR,
        OptimizerAlgorithm.CHEAPEST_INSERTION,
        OptimizerAlgorithm.LOCAL_SEARCH,
        OptimizerAlgorithm.SIMULATED_ANNEALING,
        OptimizerAlgorithm.GENETIC,
        OptimizerAlgorithm.ORTOOLS,
    ]
    baseline = _baseline_distance_km(request)
    results: list[OptimizationResult] = []
    for algorithm in wanted:
        if algorithm is OptimizerAlgorithm.AUTO:
            continue
        started = time.perf_counter()
        if algorithm is OptimizerAlgorithm.ORTOOLS:
            result = _solve_with_ortools(request) if ORTOOLS_AVAILABLE else None
            if result is None or not result.feasible:
                result = _solve_greedy(request)
        else:
            result = algorithms.solve(request, algorithm)
        result.objective_value = algorithms.objective_of(request, result)
        result.distance_before_km = baseline
        result.duration_seconds = round(time.perf_counter() - started, 3)
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# OR-Tools model
# ---------------------------------------------------------------------------


def _solve_with_ortools(request: OptimizationRequest) -> OptimizationResult | None:
    started = time.perf_counter()

    places = request.places
    days = request.days
    num_days = len(days)

    # Node 0 is the depot (the traveller's base); nodes 1..n are places.
    coords: list[Coordinate] = [request.origin] + [p.coordinate for p in places]
    num_nodes = len(coords)

    distance_km, duration_min = build_travel_matrix(coords, request.mode)
    # Metres keep the distance dimension integral without losing precision.
    distance_m = (distance_km * 1000.0).astype(int)

    manager = pywrapcp.RoutingIndexManager(
        num_nodes, num_days, [0] * num_days, [0] * num_days
    )
    routing = pywrapcp.RoutingModel(manager)

    def service_minutes(node: int) -> int:
        """Time spent at ``node``. The depot costs nothing."""
        return 0 if node == 0 else places[node - 1].visit_minutes

    def time_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(duration_min[from_node][to_node]) + service_minutes(from_node)

    def distance_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(distance_m[from_node][to_node])

    time_cb = routing.RegisterTransitCallback(time_callback)
    dist_cb = routing.RegisterTransitCallback(distance_callback)

    # Travel time is the thing being minimised.
    routing.SetArcCostEvaluatorOfAllVehicles(time_cb)

    horizon = num_days * MINUTES_PER_DAY
    routing.AddDimension(
        time_cb,
        request.max_wait_minutes,  # slack: permitted waiting before an opening
        horizon,
        False,  # do not force start cumul to zero - each day starts at its own offset
        "Time",
    )
    time_dim = routing.GetDimensionOrDie("Time")

    # Cap how far any single day may range.
    max_day_m = max(1000, int(request.max_daily_distance_km * 1000))
    routing.AddDimensionWithVehicleCapacity(
        dist_cb, 0, [max_day_m] * num_days, True, "Distance"
    )

    # Cap places per day. The depot is counted too, hence the +1.
    routing.AddConstantDimension(1, request.max_places_per_day + 1, True, "Count")

    _apply_vehicle_windows(routing, time_dim, days)
    unschedulable = _apply_node_windows(manager, routing, time_dim, request)
    _apply_disjunctions(manager, routing, request, unschedulable)

    search = pywrapcp.DefaultRoutingSearchParameters()
    search.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search.time_limit.FromSeconds(max(1, request.time_limit_seconds))
    search.log_search = False

    with OPTIMIZER_DURATION.time():
        assignment = routing.SolveWithParameters(search)

    elapsed = time.perf_counter() - started
    if assignment is None:
        logger.warning(
            "ortools_no_assignment",
            status=routing.status(),
            elapsed_seconds=round(elapsed, 3),
        )
        return None

    result = _extract_solution(manager, routing, assignment, time_dim, request, distance_km)
    result.status = _status_name(routing.status())
    result.objective_value = assignment.ObjectiveValue()
    result.duration_seconds = round(elapsed, 3)
    result.engine = "ortools"

    logger.info(
        "optimizer_solved",
        status=result.status,
        scheduled=result.scheduled_count,
        dropped=len(result.dropped),
        distance_km=result.total_distance_km,
        elapsed_seconds=result.duration_seconds,
    )
    return result


def _apply_vehicle_windows(routing: Any, time_dim: Any, days: list[Any]) -> None:
    """Pin each vehicle (day) to its slice of the global timeline."""
    for vehicle, day in enumerate(days):
        offset = day.index * MINUTES_PER_DAY
        start = offset + day.start_minutes
        end = offset + day.end_minutes

        time_dim.CumulVar(routing.Start(vehicle)).SetRange(start, end)
        time_dim.CumulVar(routing.End(vehicle)).SetRange(start, end)

        # Finish each day as early as the plan allows, so free time lands at the
        # end of the day rather than as dead gaps in the middle.
        routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.End(vehicle)))
        routing.AddVariableMaximizedByFinalizer(time_dim.CumulVar(routing.Start(vehicle)))


def _apply_node_windows(
    manager: Any, routing: Any, time_dim: Any, request: OptimizationRequest
) -> set[int]:
    """Restrict every place to the minutes when it is actually visitable.

    A node's domain is the union, across all trip days, of that day's active
    hours intersected with the place's opening hours (minus the visit duration,
    so the visit ends before closing). Because OR-Tools cumul variables take a
    single range, the union is expressed as one broad range with the gaps
    removed via ``RemoveInterval``.

    Returns:
        The node indices that are visitable on no day of this trip. These are
        forced unperformed here, and the caller must not add a second, priced
        disjunction for them - two disjunctions over the same node let the
        priced one win, which is exactly how a closed place ends up scheduled.
    """
    days = request.days
    unschedulable: set[int] = set()

    for node, place in enumerate(request.places, start=1):
        index = manager.NodeToIndex(node)
        cumul = time_dim.CumulVar(index)

        allowed: list[tuple[int, int]] = []
        for day in days:
            window = place.window_for(day.weekday)
            if window is None:
                continue
            opens, closes = window
            offset = day.index * MINUTES_PER_DAY
            earliest = offset + max(day.start_minutes, opens)
            # The visit must finish before whichever comes first: closing time or
            # the end of the traveller's day.
            latest = offset + min(day.end_minutes, closes) - place.visit_minutes
            if latest >= earliest:
                allowed.append((earliest, latest))

        if not allowed:
            # Closed for the entire trip. A zero-penalty disjunction only makes
            # dropping free, not mandatory; pinning the vehicle variable to -1
            # ("served by no vehicle") is what actually forbids the visit.
            routing.AddDisjunction([index], 0)
            routing.VehicleVar(index).SetValues([-1])
            unschedulable.add(node)
            continue

        allowed.sort()
        cumul.SetRange(allowed[0][0], allowed[-1][1])
        for (_, previous_end), (next_start, _) in pairwise(allowed):
            if next_start > previous_end + 1:
                cumul.RemoveInterval(previous_end + 1, next_start - 1)

    return unschedulable


def _apply_disjunctions(
    manager: Any,
    routing: Any,
    request: OptimizationRequest,
    unschedulable: set[int],
) -> None:
    """Make non-mandatory places droppable, priced by how good they are."""
    for node, place in enumerate(request.places, start=1):
        if node in unschedulable:
            # Already forced unperformed by _apply_node_windows.
            continue
        index = manager.NodeToIndex(node)
        if place.mandatory:
            # Still a disjunction, but priced so high that dropping it is only
            # chosen when the alternative is no solution at all.
            routing.AddDisjunction([index], MANDATORY_UNSERVED_PENALTY)
            continue
        score = max(0.0, min(1.0, place.score))
        penalty = DROP_PENALTY_BASE + int(DROP_PENALTY_SCORE_WEIGHT * score)
        routing.AddDisjunction([index], penalty)


def _extract_solution(
    manager: Any,
    routing: Any,
    assignment: Any,
    time_dim: Any,
    request: OptimizationRequest,
    distance_km: Any,
) -> OptimizationResult:
    """Walk each vehicle's route and turn it into day schedules."""
    days: list[DaySchedule] = []
    visited: set[int] = set()

    for vehicle, day in enumerate(request.days):
        schedule = DaySchedule(day_index=day.index)
        offset = day.index * MINUTES_PER_DAY

        index = routing.Start(vehicle)
        previous_node = manager.IndexToNode(index)

        while not routing.IsEnd(index):
            index = assignment.Value(routing.NextVar(index))
            node = manager.IndexToNode(index)
            if routing.IsEnd(index) or node == 0:
                break

            place = request.places[node - 1]
            arrival = assignment.Value(time_dim.CumulVar(index)) - offset
            leg_km = float(distance_km[previous_node][node])
            leg_mode = effective_mode(leg_km, request.mode)

            schedule.stops.append(
                ScheduledStop(
                    place_index=node - 1,
                    arrival_minutes=arrival,
                    departure_minutes=arrival + place.visit_minutes,
                    travel_km=round(leg_km, 3),
                    travel_minutes=_leg_minutes(previous_node, node, request, leg_km),
                    mode=leg_mode,
                )
            )
            schedule.total_distance_km += leg_km
            visited.add(node - 1)
            previous_node = node

        schedule.total_distance_km = round(schedule.total_distance_km, 3)
        schedule.total_travel_minutes = sum(s.travel_minutes for s in schedule.stops)
        days.append(schedule)

    dropped = [i for i in range(len(request.places)) if i not in visited]
    return OptimizationResult(days=days, dropped=dropped)


def _leg_minutes(
    from_node: int, to_node: int, request: OptimizationRequest, distance: float
) -> int:
    from app.optimization.distance import travel_minutes

    if from_node == to_node:
        return 0
    return travel_minutes(distance, effective_mode(distance, request.mode))


def _status_name(status: int) -> str:
    names = {
        0: "not_solved",
        1: "success",
        2: "fail",
        3: "fail_timeout",
        4: "invalid",
        5: "infeasible",
    }
    return names.get(status, f"unknown_{status}")


# ---------------------------------------------------------------------------
# Greedy fallback
# ---------------------------------------------------------------------------


def _solve_greedy(request: OptimizationRequest) -> OptimizationResult:
    """Nearest-neighbour construction with a 2-opt pass.

    Used when OR-Tools is unavailable or returns nothing. Respects the pace cap,
    opening hours and the daily time window, but makes no claim to optimality.
    """
    started = time.perf_counter()

    remaining = sorted(
        range(len(request.places)),
        key=lambda i: (not request.places[i].mandatory, -request.places[i].score),
    )
    days: list[DaySchedule] = []
    scheduled: set[int] = set()

    for day in request.days:
        schedule = DaySchedule(day_index=day.index)
        clock = day.start_minutes
        current = request.origin
        picked: list[int] = []

        while len(picked) < request.max_places_per_day:
            candidate = _next_greedy_stop(
                request, remaining, scheduled, current, clock, day
            )
            if candidate is None:
                break

            index, arrival, leg_km, leg_minutes = candidate
            place = request.places[index]
            schedule.stops.append(
                ScheduledStop(
                    place_index=index,
                    arrival_minutes=arrival,
                    departure_minutes=arrival + place.visit_minutes,
                    travel_km=round(leg_km, 3),
                    travel_minutes=leg_minutes,
                    mode=effective_mode(leg_km, request.mode),
                )
            )
            schedule.total_distance_km += leg_km
            clock = arrival + place.visit_minutes
            current = place.coordinate
            scheduled.add(index)
            picked.append(index)

        schedule.total_distance_km = round(schedule.total_distance_km, 3)
        schedule.total_travel_minutes = sum(s.travel_minutes for s in schedule.stops)
        days.append(schedule)

    _two_opt_days(request, days)

    return OptimizationResult(
        days=days,
        dropped=[i for i in range(len(request.places)) if i not in scheduled],
        status="greedy",
        duration_seconds=round(time.perf_counter() - started, 3),
        engine="greedy",
    )


def _next_greedy_stop(
    request: OptimizationRequest,
    order: list[int],
    scheduled: set[int],
    current: Coordinate,
    clock: int,
    day: Any,
) -> tuple[int, int, float, int] | None:
    """Pick the cheapest feasible next stop, or None when the day is full."""
    from app.optimization.distance import travel_minutes

    best: tuple[int, int, float, int] | None = None
    best_cost = float("inf")

    for index in order:
        if index in scheduled:
            continue
        place = request.places[index]
        window = place.window_for(day.weekday)
        if window is None:
            continue

        leg_km = haversine_km(current, place.coordinate) * settings.road_distance_factor
        leg_minutes = travel_minutes(leg_km, effective_mode(leg_km, request.mode))
        arrival = max(clock + leg_minutes, day.start_minutes, window[0])

        if arrival + place.visit_minutes > min(day.end_minutes, window[1]):
            continue

        # Prefer near and desirable; the score term is scaled into minutes so the
        # two are comparable.
        cost = leg_minutes - place.score * 60.0
        if place.mandatory:
            cost -= 1000.0
        if cost < best_cost:
            best_cost = cost
            best = (index, arrival, leg_km, leg_minutes)

    return best


def _two_opt_days(request: OptimizationRequest, days: list[DaySchedule]) -> None:
    """Reverse-segment improvement on each day's visiting order.

    Only the geometry is improved here; arrival times are recomputed afterwards
    and the swap is rejected if it breaks an opening-hours constraint.
    """
    for schedule in days:
        if len(schedule.stops) < 4:
            continue
        day = next(d for d in request.days if d.index == schedule.day_index)
        order = [s.place_index for s in schedule.stops]
        improved = True

        while improved:
            improved = False
            for i in range(len(order) - 1):
                for j in range(i + 2, len(order)):
                    candidate = order[: i + 1] + order[i + 1 : j + 1][::-1] + order[j + 1 :]
                    if _route_length(request, candidate) < _route_length(
                        request, order
                    ) and _feasible(request, candidate, day):
                        order = candidate
                        improved = True
        _rewrite_day(request, schedule, order, day)


def _route_length(request: OptimizationRequest, order: list[int]) -> float:
    total = 0.0
    current = request.origin
    for index in order:
        total += haversine_km(current, request.places[index].coordinate)
        current = request.places[index].coordinate
    return total


def _feasible(request: OptimizationRequest, order: list[int], day: Any) -> bool:
    from app.optimization.distance import travel_minutes

    clock = day.start_minutes
    current = request.origin
    for index in order:
        place = request.places[index]
        window = place.window_for(day.weekday)
        if window is None:
            return False
        leg_km = haversine_km(current, place.coordinate) * settings.road_distance_factor
        clock = max(
            clock + travel_minutes(leg_km, effective_mode(leg_km, request.mode)),
            window[0],
        )
        if clock + place.visit_minutes > min(day.end_minutes, window[1]):
            return False
        clock += place.visit_minutes
        current = place.coordinate
    return True


def _rewrite_day(
    request: OptimizationRequest, schedule: DaySchedule, order: list[int], day: Any
) -> None:
    from app.optimization.distance import travel_minutes

    clock = day.start_minutes
    current = request.origin
    stops: list[ScheduledStop] = []
    total_km = 0.0

    for index in order:
        place = request.places[index]
        window = place.window_for(day.weekday) or (0, MINUTES_PER_DAY)
        leg_km = haversine_km(current, place.coordinate) * settings.road_distance_factor
        mode = effective_mode(leg_km, request.mode)
        leg_minutes = travel_minutes(leg_km, mode)
        arrival = max(clock + leg_minutes, window[0])

        stops.append(
            ScheduledStop(
                place_index=index,
                arrival_minutes=arrival,
                departure_minutes=arrival + place.visit_minutes,
                travel_km=round(leg_km, 3),
                travel_minutes=leg_minutes,
                mode=mode,
            )
        )
        total_km += leg_km
        clock = arrival + place.visit_minutes
        current = place.coordinate

    schedule.stops = stops
    schedule.total_distance_km = round(total_km, 3)
    schedule.total_travel_minutes = sum(s.travel_minutes for s in stops)


def _baseline_distance_km(request: OptimizationRequest) -> float:
    """Distance of visiting places in their incoming order, for a before/after figure."""
    limit = request.max_places_per_day * len(request.days)
    order = list(range(min(limit, len(request.places))))
    if not order:
        return 0.0
    total = 0.0
    current = request.origin
    for index in order:
        total += haversine_km(current, request.places[index].coordinate)
        current = request.places[index].coordinate
    return round(total * settings.road_distance_factor, 3)
