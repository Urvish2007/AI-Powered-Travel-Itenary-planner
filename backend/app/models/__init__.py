"""Beanie document models.

``ALL_DOCUMENTS`` is the registration list handed to ``beanie.init_beanie``.
Every new Document must be added there, or its collection and indexes will
never be initialised.
"""

from app.models.ai_run import AIRun
from app.models.base import GeoPoint, TimestampedDocument
from app.models.itinerary import (
    Activity,
    GenerationMeta,
    Itinerary,
    ItineraryDay,
    OptimizationReport,
    TravelLeg,
)
from app.models.place import OpeningHours, Place, PlaceCost
from app.models.trip import Destination, PartyComposition, Trip, TripConstraints
from app.models.user import RefreshToken, TravelPreferences, User

ALL_DOCUMENTS = [User, RefreshToken, Place, Trip, Itinerary, AIRun]

__all__ = [
    "ALL_DOCUMENTS",
    "AIRun",
    "Activity",
    "Destination",
    "GenerationMeta",
    "GeoPoint",
    "Itinerary",
    "ItineraryDay",
    "OpeningHours",
    "OptimizationReport",
    "PartyComposition",
    "Place",
    "PlaceCost",
    "RefreshToken",
    "TimestampedDocument",
    "TravelLeg",
    "TravelPreferences",
    "Trip",
    "TripConstraints",
    "User",
]
