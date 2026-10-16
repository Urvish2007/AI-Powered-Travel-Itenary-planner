"""The itinerary planning pipeline.

Implements the flow the architecture report specifies:

    User -> LLM -> Python planning engine -> OR-Tools -> route optimisation
         -> constraint validation -> final itinerary

Each stage is a node with the signature ``(PlanningState) -> PlanningState``, so
the same functions can be run sequentially (the default, dependency-free path)
or wired into a LangGraph ``StateGraph`` (see ``app/ai/graph.py``). Keeping the
nodes free of framework types is what makes that choice reversible.

The division of labour matters: the **LLM decides what is worth doing**, and
**OR-Tools decides when and in what order**. Language models are poor at
arithmetic over time windows and travel distances; a constraint solver is poor at
knowing that a family with a toddler will hate a three-hour modern art gallery.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

from app.ai.embeddings import place_embedding_text, vector_search
from app.ai.llm import BaseLLMProvider, LLMMessage, get_llm_provider
from app.ai.prompts import (
    NARRATE_SYSTEM,
    PARSE_INTENT_SYSTEM,
    SELECT_PLACES_SYSTEM,
    build_intent_prompt,
    build_narration_prompt,
    build_selection_prompt,
)
from app.ai.schemas import NarrationResult, PlaceSelectionResult, TripIntent
from app.core.exceptions import ExternalServiceError, OptimizationError
from app.core.timeutils import to_clock, to_minutes
from app.models.enums import (
    ActivityKind,
    ItineraryStatus,
    PlaceCategory,
    TravelPace,
)
from app.models.itinerary import (
    Activity,
    GenerationMeta,
    Itinerary,
    ItineraryDay,
    OptimizationReport,
    TravelLeg,
)
from app.models.place import Place
from app.models.trip import Trip
from app.observability.logging import get_logger
from app.observability.metrics import ITINERARIES_GENERATED
from app.optimization.distance import Coordinate
from app.optimization.engine import optimize
from app.optimization.schemas import (
    DayConfig,
    OptimizationPlace,
    OptimizationRequest,
    OptimizationResult,
)
from app.services import routing_service

logger = get_logger(__name__)

# How many places a day holds at each pace. These are the single lever that
# makes "relaxed" actually feel relaxed.
PACE_PLACES_PER_DAY: dict[TravelPace, int] = {
    TravelPace.RELAXED: 3,
    TravelPace.MODERATE: 5,
    TravelPace.PACKED: 7,
}

# Upper bound on candidates handed to the LLM. Beyond this the prompt gets long
# and expensive without improving selection quality.
MAX_CANDIDATES = 60

# Categories treated as meals rather than sights, so they are not double-counted
# against the day's sightseeing budget.
MEAL_CATEGORIES = {PlaceCategory.RESTAURANT, PlaceCategory.CAFE}

LUNCH_WINDOW = (to_minutes("12:00"), to_minutes("14:30"))
DINNER_WINDOW = (to_minutes("18:30"), to_minutes("21:00"))
MEAL_DURATION_MINUTES = 60


@dataclass
class PlanningState:
    """Everything the pipeline accumulates as it runs."""

    trip: Trip
    brief: str = ""
    intent: TripIntent | None = None
    candidates: list[Place] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    selected: list[Place] = field(default_factory=list)
    optimization: OptimizationResult | None = None
    days: list[ItineraryDay] = field(default_factory=list)
    narration: NarrationResult | None = None
    warnings: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    provider: str = "stub"
    model: str = "stub"
    # How travel was priced: measured by a routing provider, or estimated.
    travel_estimated: bool = True
    routing_provider: str = "haversine"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    started_at: float = field(default_factory=time.perf_counter)

    def record(self, name: str, **details: Any) -> None:
        self.steps.append(
            {
                "step": name,
                "elapsed_ms": int((time.perf_counter() - self.started_at) * 1000),
                **details,
            }
        )

    def account(self, response: Any) -> None:
        self.provider = response.provider
        self.model = response.model
        self.prompt_tokens += response.prompt_tokens
        self.completion_tokens += response.completion_tokens


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def parse_intent(
    state: PlanningState, llm: BaseLLMProvider | None = None
) -> PlanningState:
    """Resolve the free-text brief into structured planning parameters.

    A trip created purely through structured form fields has no brief; in that
    case the constraints already on the trip are authoritative and the LLM is
    skipped entirely.
    """
    if not state.brief.strip():
        state.intent = TripIntent(
            interests=list(state.trip.constraints.interests),
            pace=state.trip.constraints.pace,
            budget_level=state.trip.constraints.budget_level,
        )
        state.record("parse_intent", skipped=True, reason="no free-text brief")
        return state

    llm = llm or get_llm_provider()
    try:
        intent, response = await llm.complete_json(
            [LLMMessage(role="user", content=build_intent_prompt(state.brief, state.trip))],
            TripIntent,
            system=PARSE_INTENT_SYSTEM,
        )
    except ExternalServiceError as exc:
        state.warnings.append(f"Intent parsing failed, using trip defaults: {exc.message}")
        state.intent = TripIntent(
            interests=list(state.trip.constraints.interests),
            pace=state.trip.constraints.pace,
            budget_level=state.trip.constraints.budget_level,
        )
        state.record("parse_intent", ok=False, error=exc.message)
        return state

    state.intent = intent
    state.account(response)
    state.record(
        "parse_intent",
        ok=True,
        interests=[str(i) for i in intent.interests],
        pace=str(intent.pace),
    )
    return state


async def retrieve_candidates(state: PlanningState) -> PlanningState:
    """Gather candidate places near the destination.

    Two recall paths are unioned:

    * a ``$near`` geospatial query, which guarantees geographic relevance;
    * a semantic vector query built from the traveller's interests, which
      surfaces places whose text matches the brief even when they sit further out.

    Union-then-rank beats either alone: geo-only misses thematic fits, and
    vector-only can wander out of the city.
    """
    trip = state.trip
    lat, lng = trip.destination.location.as_lat_lng()

    geo_query = {
        "is_active": True,
        "location": {
            "$near": {
                "$geometry": {"type": "Point", "coordinates": [lng, lat]},
                "$maxDistance": 40_000,  # metres; a generous city radius
            }
        },
    }
    if trip.constraints.must_avoid_place_ids:
        geo_query["_id"] = {"$nin": trip.constraints.must_avoid_place_ids}

    nearby = await Place.find(geo_query).limit(MAX_CANDIDATES).to_list()

    interests = ([str(i) for i in state.intent.interests] if state.intent else []) or [
        str(i) for i in trip.constraints.interests
    ]
    semantic: list[Place] = []
    if interests:
        query_text = (
            f"{trip.destination.city} {' '.join(interests)} {trip.constraints.notes or ''}"
        )
        try:
            documents = await vector_search(
                query_text, city=trip.destination.city, limit=30
            )
            semantic = [Place.model_validate(_with_id(d)) for d in documents]
        except Exception as exc:
            logger.warning("semantic_recall_failed", error=str(exc))
            state.warnings.append("Semantic search was unavailable for this request.")

    merged: dict[str, Place] = {}
    for place in [*nearby, *semantic]:
        if place.id is None:
            continue
        merged.setdefault(str(place.id), place)

    # Places the traveller insisted on are pulled in even if neither recall path
    # found them.
    for place_id in trip.constraints.must_include_place_ids:
        if str(place_id) not in merged:
            required = await Place.get(place_id)
            if required is not None:
                merged[str(required.id)] = required

    for place_id in trip.constraints.must_avoid_place_ids:
        merged.pop(str(place_id), None)

    state.candidates = list(merged.values())[:MAX_CANDIDATES]
    state.record(
        "retrieve_candidates",
        ok=True,
        geo=len(nearby),
        semantic=len(semantic),
        merged=len(state.candidates),
    )
    return state


def _with_id(document: dict[str, Any]) -> dict[str, Any]:
    """Normalise a raw Mongo document so Beanie can validate it."""
    payload = dict(document)
    payload.pop("search_score", None)
    if "_id" in payload:
        payload["id"] = payload.pop("_id")
    return payload


async def score_candidates(
    state: PlanningState, llm: BaseLLMProvider | None = None
) -> PlanningState:
    """Ask the LLM how well each candidate fits this traveller.

    The model's score is blended with objective signals (rating, popularity)
    rather than trusted outright, so a hallucinated enthusiasm for an obscure
    place cannot on its own displace a genuinely well-regarded one.
    """
    if not state.candidates:
        state.record("score_candidates", skipped=True, reason="no candidates")
        return state

    llm = llm or get_llm_provider()
    try:
        result, response = await llm.complete_json(
            [
                LLMMessage(
                    role="user",
                    content=build_selection_prompt(
                        state.trip, state.candidates, state.intent
                    ),
                )
            ],
            PlaceSelectionResult,
            system=SELECT_PLACES_SYSTEM,
        )
        state.account(response)
        llm_scores = {s.place_id: s.score for s in result.selections}
        state.reasons = {s.place_id: s.reason for s in result.selections}
        step_ok = True
    except ExternalServiceError as exc:
        state.warnings.append(f"Place scoring fell back to heuristics: {exc.message}")
        llm_scores, step_ok = {}, False

    for place in state.candidates:
        place_id = str(place.id)
        objective = 0.6 * (place.rating / 5.0) + 0.4 * place.popularity
        llm_score = llm_scores.get(place_id)
        # 60/40 in favour of the model where it gave a score: it sees the
        # traveller's intent, while the objective signal only sees the crowd.
        # With no model score, the objective signal stands alone.
        blended = objective if llm_score is None else 0.6 * llm_score + 0.4 * objective
        blended += _interest_bonus(place, state)
        state.scores[place_id] = round(max(0.0, min(1.0, blended)), 4)

    state.record(
        "score_candidates",
        ok=step_ok,
        scored=len(state.scores),
        llm_scored=len(llm_scores),
    )
    return state


def _interest_bonus(place: Place, state: PlanningState) -> float:
    """Small nudge for tag overlap with the traveller's stated interests."""
    interests = set()
    if state.intent:
        interests |= {str(i) for i in state.intent.interests}
    interests |= {str(i) for i in state.trip.constraints.interests}
    if not interests:
        return 0.0
    overlap = len(interests & {str(t) for t in place.tags})
    return min(0.15, 0.05 * overlap)


async def optimize_schedule(state: PlanningState) -> PlanningState:
    """Hand the scored candidates to OR-Tools and get back a day-by-day route."""
    trip = state.trip
    if not state.candidates:
        raise OptimizationError(
            "No places were found near this destination.",
            details={"city": trip.destination.city},
        )

    pace = state.intent.pace if state.intent else trip.constraints.pace
    per_day = PACE_PLACES_PER_DAY.get(pace, 5)

    # Meals are scheduled separately, so they are excluded from the routing pool.
    sights = [p for p in state.candidates if p.category not in MEAL_CATEGORIES]
    must_include = {str(i) for i in trip.constraints.must_include_place_ids}

    ranked = sorted(sights, key=lambda p: state.scores.get(str(p.id), 0.0), reverse=True)
    # Offer the solver roughly double what it can use, so it has genuine freedom
    # to trade a high-scoring but awkwardly placed stop for a better-fitting one.
    pool_size = max(per_day * trip.duration_days * 2, per_day * trip.duration_days + 5)
    pool = ranked[:pool_size]
    for place in ranked[pool_size:]:
        if str(place.id) in must_include:
            pool.append(place)

    if not pool:
        raise OptimizationError(
            "No sightseeing places remain after filtering.",
            details={"candidates": len(state.candidates)},
        )

    day_start = to_minutes(trip.constraints.day_start_time)
    day_end = to_minutes(trip.constraints.day_end_time)

    opt_places = [
        OptimizationPlace(
            ref=str(place.id),
            name=place.name,
            coordinate=Coordinate(*place.location.as_lat_lng()),
            visit_minutes=place.average_visit_minutes,
            score=state.scores.get(str(place.id), 0.5),
            cost=place.cost.amount,
            mandatory=str(place.id) in must_include,
            opening=_opening_map(place),
            always_open=not place.opening_hours,
            category=str(place.category),
        )
        for place in pool
    ]

    days = [
        DayConfig(
            index=offset,
            day_date=trip.start_date + timedelta(days=offset),
            weekday=(trip.start_date + timedelta(days=offset)).weekday(),
            start_minutes=day_start,
            end_minutes=day_end,
        )
        for offset in range(trip.duration_days)
    ]

    request = OptimizationRequest(
        places=opt_places,
        days=days,
        origin=Coordinate(*trip.destination.location.as_lat_lng()),
        mode=trip.constraints.preferred_transport,
        max_places_per_day=per_day,
        max_daily_distance_km=max(trip.constraints.max_daily_walking_km * 3.0, 10.0),
        time_limit_seconds=_time_limit_for(len(opt_places)),
        # The trip's own choice. `None` defers to OPTIMIZER_ALGORITHM; see
        # `engine.resolve_algorithm` for the precedence order.
        algorithm=trip.constraints.optimizer_algorithm,
    )

    # One matrix call for the whole candidate pool. A pool larger than
    # ROUTING_MAX_LOCATIONS is estimated, and the itinerary records that.
    request.matrix = await routing_service.travel_matrix(
        [request.origin] + [p.coordinate for p in request.places], request.mode
    )
    state.travel_estimated = request.matrix.estimated
    state.routing_provider = request.matrix.provider

    result = optimize(request)
    if not result.feasible:
        raise OptimizationError(
            "No feasible schedule satisfies the supplied constraints. Try widening "
            "the daily time window or relaxing the pace.",
            details={
                "candidates": len(opt_places),
                "days": len(days),
                "solver_status": result.status,
            },
        )

    state.optimization = result
    state.selected = pool
    state.record(
        "optimize_schedule",
        ok=True,
        engine=result.engine,
        status=result.status,
        scheduled=result.scheduled_count,
        dropped=len(result.dropped),
        distance_km=result.total_distance_km,
    )
    return state


def _time_limit_for(place_count: int) -> int:
    """Scale the solver budget with problem size, within configured bounds."""
    from app.core.config import settings

    base = settings.optimizer_time_limit_seconds
    if place_count <= 20:
        return max(1, base // 2)
    if place_count <= 60:
        return base
    return base * 2


def _opening_map(place: Place) -> dict[int, tuple[int, int]]:
    """Convert stored opening hours into solver-friendly minute windows."""
    windows: dict[int, tuple[int, int]] = {}
    for window in place.opening_hours:
        if window.closed or not window.opens or not window.closes:
            continue
        opens, closes = to_minutes(window.opens), to_minutes(window.closes)
        # A window like 20:00-02:00 crosses midnight; clamp it to the day rather
        # than emitting a negative-length window the solver would reject.
        if closes <= opens:
            closes = to_minutes("23:59")
        windows[window.day_of_week] = (opens, closes)
    return windows


async def assemble_itinerary(state: PlanningState) -> PlanningState:
    """Turn the solver's routes into activity blocks, and slot meals into gaps."""
    trip = state.trip
    result = state.optimization
    if result is None:
        raise OptimizationError("assemble_itinerary ran before optimize_schedule.")

    restaurants = [p for p in state.candidates if p.category in MEAL_CATEGORIES]
    currency = trip.constraints.currency

    days: list[ItineraryDay] = []
    for day_schedule in result.days:
        day_date = trip.start_date + timedelta(days=day_schedule.day_index)
        activities: list[Activity] = []

        for stop in day_schedule.stops:
            place = _place_for_stop(state, stop.place_index)
            if place is None:
                continue

            leg = (
                TravelLeg(
                    mode=stop.mode,
                    distance_km=round(stop.travel_km, 3),
                    duration_minutes=stop.travel_minutes,
                    estimated=state.travel_estimated,
                )
                if stop.travel_km > 0
                else None
            )
            activities.append(
                Activity(
                    place_id=place.id,
                    title=place.name,
                    kind=ActivityKind.VISIT,
                    description=place.description[:2000],
                    start_time=to_clock(stop.arrival_minutes),
                    end_time=to_clock(stop.departure_minutes),
                    duration_minutes=stop.departure_minutes - stop.arrival_minutes,
                    location=place.location,
                    address=place.address,
                    estimated_cost=round(place.cost.amount * trip.party.adults, 2),
                    currency=currency,
                    rationale=state.reasons.get(str(place.id), ""),
                    travel_from_previous=leg,
                )
            )

        activities = _insert_meals(activities, restaurants, trip, day_date)
        for order, activity in enumerate(activities):
            activity.order = order

        days.append(
            ItineraryDay(
                day_number=day_schedule.day_index + 1,
                day_date=day_date,
                activities=activities,
            )
        )

    state.days = days
    state.record(
        "assemble_itinerary",
        ok=True,
        days=len(days),
        activities=sum(len(d.activities) for d in days),
    )
    return state


def _place_for_stop(state: PlanningState, place_index: int) -> Place | None:
    """Resolve a solver stop index back to its Place document.

    The solver's place list is built from ``state.selected`` in order, so the
    index maps straight back. Guarded anyway, because an out-of-range index here
    would mean the two lists had drifted apart.
    """
    if 0 <= place_index < len(state.selected):
        return state.selected[place_index]
    logger.warning("stop_index_out_of_range", index=place_index, pool=len(state.selected))
    return None


def _insert_meals(
    activities: list[Activity],
    restaurants: list[Place],
    trip: Trip,
    day_date: date,
) -> list[Activity]:
    """Drop a lunch and a dinner into the day's natural gaps.

    A meal is only inserted when a real gap exists: the point is to use time the
    optimiser left free, not to displace something the traveller chose.
    """
    if not restaurants:
        return activities

    used: set[str] = set()
    result = list(activities)

    for window, label in ((LUNCH_WINDOW, "Lunch"), (DINNER_WINDOW, "Dinner")):
        slot = _find_gap(result, window, MEAL_DURATION_MINUTES)
        if slot is None:
            continue
        start, anchor = slot
        restaurant = _nearest_unused(restaurants, anchor, used)
        if restaurant is None:
            continue
        used.add(str(restaurant.id))

        result.append(
            Activity(
                place_id=restaurant.id,
                title=f"{label} at {restaurant.name}",
                kind=ActivityKind.MEAL,
                description=restaurant.description[:500],
                start_time=to_clock(start),
                end_time=to_clock(start + MEAL_DURATION_MINUTES),
                duration_minutes=MEAL_DURATION_MINUTES,
                location=restaurant.location,
                address=restaurant.address,
                estimated_cost=round(restaurant.cost.amount * trip.party.total, 2),
                currency=trip.constraints.currency,
                rationale=f"A short walk from your {window[0] // 60}:00 activity.",
            )
        )

    result.sort(key=lambda a: a.start_time)
    return result


def _find_gap(
    activities: list[Activity], window: tuple[int, int], needed: int
) -> tuple[int, Any] | None:
    """Find a free stretch of ``needed`` minutes inside ``window``.

    Returns the start minute and the activity the meal should sit near, so the
    restaurant can be chosen by proximity to that activity.
    """
    window_start, window_end = window
    if not activities:
        return window_start, None

    ordered = sorted(activities, key=lambda a: a.start_time)
    # Before the first activity of the day.
    first_start = to_minutes(ordered[0].start_time)
    if first_start - needed >= window_start and first_start <= window_end:
        return max(window_start, first_start - needed), ordered[0]

    for current, following in pairwise(ordered):
        gap_start = to_minutes(current.end_time)
        gap_end = to_minutes(following.start_time)
        if gap_end - gap_start < needed:
            continue
        start = max(gap_start, window_start)
        if start + needed <= min(gap_end, window_end):
            return start, current

    # After the last activity.
    last_end = to_minutes(ordered[-1].end_time)
    if window_start <= last_end <= window_end - needed:
        return last_end, ordered[-1]
    if last_end < window_start <= window_end - needed:
        return window_start, ordered[-1]
    return None


def _nearest_unused(restaurants: list[Place], anchor: Any, used: set[str]) -> Place | None:
    """Closest unused restaurant to ``anchor``, or the best-rated when unanchored."""
    from app.optimization.distance import haversine_km

    available = [r for r in restaurants if str(r.id) not in used]
    if not available:
        return None
    if anchor is None or anchor.location is None:
        return max(available, key=lambda r: r.rating)

    origin = Coordinate(*anchor.location.as_lat_lng())
    return min(
        available,
        key=lambda r: haversine_km(origin, Coordinate(*r.location.as_lat_lng())),
    )


async def narrate(
    state: PlanningState, llm: BaseLLMProvider | None = None
) -> PlanningState:
    """Write the user-facing titles and summaries over the finished schedule."""
    if not state.days:
        state.record("narrate", skipped=True, reason="no days")
        return state

    payloads = [
        {
            "day_number": day.day_number,
            "date": day.day_date.isoformat(),
            "distance_km": day.total_distance_km,
            "activities": [
                {
                    "title": a.title,
                    "start_time": a.start_time,
                    "end_time": a.end_time,
                    "category": str(a.kind),
                }
                for a in day.activities
            ],
        }
        for day in state.days
    ]

    llm = llm or get_llm_provider()
    try:
        narration, response = await llm.complete_json(
            [
                LLMMessage(
                    role="user",
                    content=build_narration_prompt(state.trip, payloads),
                )
            ],
            NarrationResult,
            system=NARRATE_SYSTEM,
        )
        state.account(response)
    except ExternalServiceError as exc:
        state.warnings.append(f"Narration failed: {exc.message}")
        state.record("narrate", ok=False, error=exc.message)
        return state

    state.narration = narration
    by_number = {d.day_number: d for d in narration.days}
    for day in state.days:
        written = by_number.get(day.day_number)
        if written:
            day.title = written.title or f"Day {day.day_number}"
            day.summary = written.summary
        else:
            day.title = f"Day {day.day_number}"

    state.record("narrate", ok=True, days=len(narration.days))
    return state


async def validate_plan(state: PlanningState) -> PlanningState:
    """Post-solve sanity checks.

    The solver already enforces its constraints, so anything caught here is a bug
    in how the model was built or in how the result was assembled. Findings are
    recorded as warnings rather than raised, so the traveller still gets a plan.
    """
    trip = state.trip
    day_start = to_minutes(trip.constraints.day_start_time)
    day_end = to_minutes(trip.constraints.day_end_time)
    walking_cap = trip.constraints.max_daily_walking_km

    for day in state.days:
        previous_end = day_start
        for activity in day.activities:
            start, end = to_minutes(activity.start_time), to_minutes(activity.end_time)
            if start < day_start or end > day_end:
                state.warnings.append(
                    f"Day {day.day_number}: {activity.title} falls outside the "
                    f"{trip.constraints.day_start_time}-"
                    f"{trip.constraints.day_end_time} window."
                )
            if start < previous_end:
                state.warnings.append(
                    f"Day {day.day_number}: {activity.title} overlaps the "
                    "previous activity."
                )
            previous_end = max(previous_end, end)

        if day.total_walking_km > walking_cap + 0.5:
            state.warnings.append(
                f"Day {day.day_number}: {day.total_walking_km} km of walking exceeds "
                f"the {walking_cap} km preference."
            )

    if trip.constraints.budget_total is not None:
        total = sum(a.estimated_cost for day in state.days for a in day.activities)
        if total > trip.constraints.budget_total:
            state.warnings.append(
                f"Estimated cost {round(total, 2)} {trip.constraints.currency} exceeds "
                f"the stated budget of {trip.constraints.budget_total}."
            )

    scheduled = {
        str(a.place_id) for day in state.days for a in day.activities if a.place_id
    }
    for place_id in trip.constraints.must_include_place_ids:
        if str(place_id) not in scheduled:
            state.warnings.append(
                f"Requested place {place_id} could not be fitted into the schedule."
            )

    state.record("validate_plan", ok=True, warnings=len(state.warnings))
    return state


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

PIPELINE_NODES = (
    ("parse_intent", parse_intent),
    ("retrieve_candidates", retrieve_candidates),
    ("score_candidates", score_candidates),
    ("optimize_schedule", optimize_schedule),
    ("assemble_itinerary", assemble_itinerary),
    ("narrate", narrate),
    ("validate_plan", validate_plan),
)


async def run_pipeline(trip: Trip, brief: str = "") -> PlanningState:
    """Execute every node in order and return the final state."""
    state = PlanningState(trip=trip, brief=brief)
    for name, node in PIPELINE_NODES:
        logger.debug("pipeline_node_start", node=name, trip_id=str(trip.id))
        state = await node(state)
    return state


def build_itinerary_document(state: PlanningState, version: int = 1) -> Itinerary:
    """Materialise the pipeline state as a persistable Itinerary."""
    trip = state.trip
    result = state.optimization

    itinerary = Itinerary(
        trip_id=trip.id,  # type: ignore[arg-type]
        user_id=trip.user_id,
        version=version,
        status=ItineraryStatus.COMPLETED,
        title=(state.narration.title if state.narration else "")
        or f"{trip.duration_days}-day trip to {trip.destination.city}",
        summary=state.narration.summary if state.narration else "",
        days=state.days,
        currency=trip.constraints.currency,
        generation=GenerationMeta(
            provider=state.provider,
            model=state.model,
            prompt_tokens=state.prompt_tokens,
            completion_tokens=state.completion_tokens,
            latency_ms=int((time.perf_counter() - state.started_at) * 1000),
            warnings=state.warnings,
        ),
    )

    if result is not None:
        dropped_names = {}
        for index in result.dropped[:20]:
            if 0 <= index < len(state.selected):
                place = state.selected[index]
                dropped_names[str(place.id)] = (
                    "Did not fit the day's time window or pace limit."
                )
        itinerary.optimization = OptimizationReport(
            ran=True,
            solver_status=result.status,
            objective_value=(
                float(result.objective_value)
                if result.objective_value is not None
                else None
            ),
            duration_seconds=result.duration_seconds,
            distance_before_km=result.distance_before_km,
            distance_after_km=result.total_distance_km,
            places_scheduled=result.scheduled_count,
            places_dropped=len(result.dropped),
            dropped_reasons=dropped_names,
            estimated=state.travel_estimated,
            routing_provider=state.routing_provider,
        )

    itinerary.recalculate_totals()
    ITINERARIES_GENERATED.labels(state.provider).inc()
    return itinerary


async def ensure_place_embedding(place: Place) -> bool:
    """Backfill a place's embedding when missing. Returns True when it wrote one."""
    from app.ai.embeddings import embed_text
    from app.core.config import settings

    if place.embedding and place.embedding_model == settings.embedding_model:
        return False
    text = place_embedding_text(
        place.name,
        place.description,
        str(place.category),
        [str(t) for t in place.tags],
        place.city,
    )
    place.embedding = await embed_text(text)
    place.embedding_model = settings.embedding_model
    await place.save()
    return True
