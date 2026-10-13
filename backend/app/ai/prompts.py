"""Prompt construction for the planning pipeline.

Every system prompt carries a ``TASK:`` marker on its first line. The marker is
what the deterministic stub provider keys off to decide which schema-shaped
reply to return, so prompts and stub stay in step: adding a task means adding
its marker here and its branch in ``app.ai.llm``.
"""

from __future__ import annotations

from typing import Any

from app.models.place import Place
from app.models.trip import Trip

PARSE_INTENT_SYSTEM = """TASK:PARSE_INTENT
You turn a traveller's free-text brief into structured planning parameters.

Rules:
- Infer interests only from what the brief actually says or clearly implies.
- Do not invent a budget or pace the traveller did not signal; when the brief is
  silent, use the moderate default.
- Keywords should be concrete nouns a place-search would match, not adjectives.
"""

SELECT_PLACES_SYSTEM = """TASK:SELECT_PLACES
You rank candidate places for one traveller's trip.

Score each candidate from 0 to 1 on how well it fits *this* traveller, given
their interests, pace, budget and party. Consider:
- Interest overlap: a history lover should not be handed a nightlife strip.
- Party fit: a group with young children needs family-friendly, low-effort stops.
- Budget fit: expensive tickets are a poor fit on a shoestring budget.
- Variety: an itinerary of seven near-identical museums is a worse trip than a
  mixed one, so damp down near-duplicates of things already scored highly.
- Accessibility: respect any stated access needs as a hard filter, not a nudge.

Score every candidate you are given. Do not invent ids that were not listed.
"""

NARRATE_SYSTEM = """TASK:NARRATE
You write the human-facing copy for an itinerary that has already been built and
route-optimised. The schedule is fixed - do not propose changes to it.

Write a short trip title, a two-to-three sentence trip summary, and for each day
a title and a one-to-two sentence summary. Be concrete and specific to the places
actually scheduled. No marketing language, no exclamation marks, no second-person
hype. If a day is deliberately light, say so plainly.
"""


def build_intent_prompt(brief: str, trip: Trip) -> str:
    """Prompt for turning the user's brief into structured intent."""
    return f"""Traveller brief:
\"\"\"{brief.strip()}\"\"\"

Trip facts already known:
- Destination: {trip.destination.city}, {trip.destination.country}
- Dates: {trip.start_date.isoformat()} to {trip.end_date.isoformat()}
- Length: {trip.duration_days} days
- Party: {trip.party.adults} adult(s), {trip.party.children} child(ren),
  {trip.party.infants} infant(s)
- Stated budget level: {trip.constraints.budget_level}
- Stated pace: {trip.constraints.pace}
"""


def build_selection_prompt(
    trip: Trip, places: list[Place], intent: Any | None = None
) -> str:
    """Prompt for scoring candidate places.

    Candidate ids are emitted as ``id=<objectid>`` because that is the token the
    stub provider parses, and it keeps ids unambiguous for real models too.
    """
    interests = ", ".join(trip.constraints.interests) or "not specified"
    access = ", ".join(trip.constraints.accessibility_needs) or "none"
    diet = ", ".join(trip.constraints.dietary_restrictions) or "none"

    if intent is not None and getattr(intent, "interests", None):
        interests = ", ".join(intent.interests)

    lines = [
        "Traveller profile:",
        f"- Destination: {trip.destination.city}, {trip.destination.country}",
        f"- Trip length: {trip.duration_days} day(s)",
        f"- Interests: {interests}",
        f"- Pace: {trip.constraints.pace}",
        f"- Budget level: {trip.constraints.budget_level}",
        f"- Party: {trip.party.adults} adult(s), {trip.party.children} child(ren), "
        f"{trip.party.infants} infant(s)",
        f"- Accessibility needs: {access}",
        f"- Dietary restrictions: {diet}",
    ]
    if trip.constraints.notes:
        lines.append(f"- Additional notes: {trip.constraints.notes}")

    lines.append("")
    lines.append(f"Candidate places ({len(places)}):")
    for place in places:
        tags = ", ".join(place.tags) or "none"
        cost = (
            "free" if place.cost.is_free else f"{place.cost.amount} {place.cost.currency}"
        )
        lines.append(
            f"- id={place.id} | {place.name} | category={place.category} "
            f"| tags=[{tags}] | rating={place.rating} | typical_visit="
            f"{place.average_visit_minutes}min | cost={cost}"
        )
        if place.description:
            lines.append(f"    {place.description[:220]}")

    return "\n".join(lines)


def build_narration_prompt(trip: Trip, day_payloads: list[dict[str, Any]]) -> str:
    """Prompt for writing titles and summaries over a finished schedule."""
    lines = [
        f"Trip to {trip.destination.city}, {trip.destination.country}",
        f"Dates: {trip.start_date.isoformat()} to {trip.end_date.isoformat()}",
        f"Pace: {trip.constraints.pace} | Budget: {trip.constraints.budget_level}",
        "",
        "The optimised schedule:",
    ]
    for day in day_payloads:
        lines.append(
            f"day_number={day['day_number']} date={day['date']} "
            f"({day.get('distance_km', 0)} km of travel)"
        )
        for activity in day["activities"]:
            lines.append(
                f"  {activity['start_time']}-{activity['end_time']} "
                f"{activity['title']} ({activity.get('category', 'other')})"
            )
        if not day["activities"]:
            lines.append("  (no scheduled activities)")
    return "\n".join(lines)
