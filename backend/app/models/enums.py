"""Enumerations shared across documents, schemas and the AI layer."""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class AuthProvider(StrEnum):
    """How an account can be signed into.

    An account may have several: signing in with Google on an address that
    already has a password links the two rather than creating a duplicate.
    """

    PASSWORD = "password"
    GOOGLE = "google"
    APPLE = "apple"


class TravelPace(StrEnum):
    """How much the traveller wants packed into a day."""

    RELAXED = "relaxed"
    MODERATE = "moderate"
    PACKED = "packed"


class BudgetLevel(StrEnum):
    SHOESTRING = "shoestring"
    BUDGET = "budget"
    MODERATE = "moderate"
    COMFORTABLE = "comfortable"
    LUXURY = "luxury"


class PlaceCategory(StrEnum):
    ATTRACTION = "attraction"
    MUSEUM = "museum"
    PARK = "park"
    RESTAURANT = "restaurant"
    CAFE = "cafe"
    BAR = "bar"
    SHOPPING = "shopping"
    LANDMARK = "landmark"
    BEACH = "beach"
    VIEWPOINT = "viewpoint"
    RELIGIOUS = "religious"
    NIGHTLIFE = "nightlife"
    ENTERTAINMENT = "entertainment"
    NATURE = "nature"
    HOTEL = "hotel"
    TRANSPORT = "transport"
    OTHER = "other"


class TripStatus(StrEnum):
    DRAFT = "draft"
    PLANNING = "planning"
    READY = "ready"
    ARCHIVED = "archived"


class ItineraryStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    OPTIMIZING = "optimizing"
    COMPLETED = "completed"
    FAILED = "failed"


class ActivityKind(StrEnum):
    VISIT = "visit"
    MEAL = "meal"
    TRAVEL = "travel"
    REST = "rest"
    CHECK_IN = "check_in"
    FREE_TIME = "free_time"


class TransportMode(StrEnum):
    WALKING = "walking"
    DRIVING = "driving"
    CYCLING = "cycling"
    TRANSIT = "transit"


class InterestTag(StrEnum):
    HISTORY = "history"
    ART = "art"
    FOOD = "food"
    NATURE = "nature"
    ADVENTURE = "adventure"
    NIGHTLIFE = "nightlife"
    SHOPPING = "shopping"
    RELAXATION = "relaxation"
    FAMILY = "family"
    PHOTOGRAPHY = "photography"
    ARCHITECTURE = "architecture"
    CULTURE = "culture"
    SPORTS = "sports"
    MUSIC = "music"


class AccessibilityNeed(StrEnum):
    WHEELCHAIR = "wheelchair"
    STEP_FREE = "step_free"
    LOW_WALKING = "low_walking"
    HEARING = "hearing"
    VISUAL = "visual"


class AIRunStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
