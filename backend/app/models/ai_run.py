"""Audit trail for AI planning runs.

Kept separate from the itinerary so that failed or experimental runs can be
analysed without polluting user-visible data. This is the collection to query
when asking why the planner made a given choice, or when evaluating whether a
prompt change helped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.models.base import utcnow
from app.models.enums import AIRunStatus


class AIRun(Document):
    trip_id: PydanticObjectId | None = None
    itinerary_id: PydanticObjectId | None = None
    user_id: PydanticObjectId | None = None
    kind: str = Field(default="generate_itinerary", max_length=64)
    provider: str = "stub"
    model: str = "stub"
    status: AIRunStatus = AIRunStatus.STARTED
    steps: list[dict[str, Any]] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "ai_runs"
        indexes = [
            IndexModel([("trip_id", pymongo.ASCENDING)], name="ai_run_trip"),
            IndexModel([("created_at", pymongo.DESCENDING)], name="ai_run_recent"),
            IndexModel([("status", pymongo.ASCENDING)], name="ai_run_status"),
        ]
