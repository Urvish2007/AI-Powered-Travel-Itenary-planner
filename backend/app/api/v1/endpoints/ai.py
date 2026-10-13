"""AI-facing endpoints: intent parsing, recommendations and run introspection."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.ai.llm import LLMMessage, get_llm_provider
from app.ai.prompts import PARSE_INTENT_SYSTEM, build_intent_prompt
from app.ai.schemas import TripIntent
from app.core.deps import CurrentUser
from app.models.ai_run import AIRun
from app.schemas.place import PlacePublic
from app.services import place_service, trip_service

router = APIRouter(prefix="/ai", tags=["ai"])


class ParseIntentRequest(BaseModel):
    trip_id: str = Field(min_length=1)
    brief: str = Field(min_length=1, max_length=4000)


class RecommendRequest(BaseModel):
    """Ask for places matching a free-text description."""

    query: str = Field(min_length=2, max_length=500)
    city: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=10, ge=1, le=30)


@router.post(
    "/parse-intent",
    response_model=TripIntent,
    summary="Turn a free-text brief into structured planning parameters",
    description=(
        "Exposed separately from generation so the UI can show the traveller how "
        "their words were interpreted - and let them correct it - before a full "
        "plan is built."
    ),
)
async def parse_intent(payload: ParseIntentRequest, user: CurrentUser) -> TripIntent:
    trip = await trip_service.get_owned_trip(payload.trip_id, user)
    llm = get_llm_provider()
    intent, _ = await llm.complete_json(
        [LLMMessage(role="user", content=build_intent_prompt(payload.brief, trip))],
        TripIntent,
        system=PARSE_INTENT_SYSTEM,
    )
    return intent


@router.post(
    "/recommend",
    response_model=list[PlacePublic],
    summary="Recommend places from a natural-language description",
)
async def recommend(payload: RecommendRequest, user: CurrentUser) -> list[PlacePublic]:
    pairs = await place_service.semantic_search(
        payload.query, city=payload.city, limit=payload.limit
    )
    results = []
    for place, score in pairs:
        item = PlacePublic.model_validate(place)
        item.search_score = score
        results.append(item)
    return results


@router.get(
    "/runs",
    summary="Recent planning runs for the caller",
    description=(
        "The audit trail behind each generated itinerary: which provider ran, "
        "how long each pipeline stage took, and what it decided. This is what "
        "makes a surprising itinerary explainable after the fact."
    ),
)
async def list_runs(
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[dict]:
    runs = (
        await AIRun.find(AIRun.user_id == user.id)
        .sort(-AIRun.created_at)
        .limit(limit)
        .to_list()
    )
    return [
        {
            "id": str(run.id),
            "trip_id": str(run.trip_id) if run.trip_id else None,
            "itinerary_id": str(run.itinerary_id) if run.itinerary_id else None,
            "kind": run.kind,
            "provider": run.provider,
            "model": run.model,
            "status": str(run.status),
            "latency_ms": run.latency_ms,
            "prompt_tokens": run.prompt_tokens,
            "completion_tokens": run.completion_tokens,
            "steps": run.steps,
            "error": run.error,
            "created_at": run.created_at.isoformat(),
        }
        for run in runs
    ]
