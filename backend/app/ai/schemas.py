"""Structured outputs the LLM is asked to produce.

Each schema doubles as the JSON Schema handed to the model and as the validator
for what comes back, so a malformed reply fails loudly at the boundary rather
than silently corrupting an itinerary.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import BudgetLevel, InterestTag, TravelPace


class TripIntent(BaseModel):
    """A free-text brief, resolved into planner-ready parameters."""

    interests: list[InterestTag] = Field(default_factory=list)
    pace: TravelPace = TravelPace.MODERATE
    budget_level: BudgetLevel = BudgetLevel.MODERATE
    keywords: list[str] = Field(default_factory=list, max_length=32)
    notes: str = Field(default="", max_length=1000)


class PlaceSelection(BaseModel):
    place_id: str = Field(min_length=1, max_length=64)
    score: float = Field(ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=500)


class PlaceSelectionResult(BaseModel):
    selections: list[PlaceSelection] = Field(default_factory=list)


class DayNarration(BaseModel):
    day_number: int = Field(ge=1)
    title: str = Field(default="", max_length=300)
    summary: str = Field(default="", max_length=2000)


class NarrationResult(BaseModel):
    title: str = Field(default="", max_length=300)
    summary: str = Field(default="", max_length=4000)
    days: list[DayNarration] = Field(default_factory=list)
