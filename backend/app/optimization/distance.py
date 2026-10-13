"""Distance and travel-time computation.

Straight-line (haversine) distance is scaled by ``road_distance_factor`` to
approximate real street distance. That keeps the optimiser dependency-free and
fast; swapping in a Mapbox Matrix API call later only requires replacing
``build_travel_matrix`` - the shape of its return value is the contract.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise

import numpy as np

from app.core.config import settings
from app.models.enums import TransportMode

EARTH_RADIUS_KM = 6371.0088

# Typical door-to-door speeds, in km/h, including the friction of getting
# around (traffic lights, waiting for a bus, parking).
MODE_SPEED_KMH: dict[TransportMode, float] = {
    TransportMode.WALKING: 4.5,
    TransportMode.CYCLING: 14.0,
    TransportMode.TRANSIT: 18.0,
    TransportMode.DRIVING: 28.0,
}

# A fixed per-leg overhead, in minutes: finding the entrance, waiting for a
# vehicle, parking. Without this, short hops look implausibly cheap.
MODE_OVERHEAD_MINUTES: dict[TransportMode, int] = {
    TransportMode.WALKING: 2,
    TransportMode.CYCLING: 4,
    TransportMode.TRANSIT: 8,
    TransportMode.DRIVING: 6,
}


@dataclass(frozen=True, slots=True)
class Coordinate:
    """A latitude/longitude pair in the human-readable order."""

    lat: float
    lng: float

    def as_tuple(self) -> tuple[float, float]:
        return self.lat, self.lng


def haversine_km(a: Coordinate, b: Coordinate) -> float:
    """Great-circle distance between two coordinates, in kilometres."""
    lat1, lng1 = math.radians(a.lat), math.radians(a.lng)
    lat2, lng2 = math.radians(b.lat), math.radians(b.lng)
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, h)))


def haversine_matrix_km(coords: list[Coordinate]) -> np.ndarray:
    """Vectorised pairwise great-circle distances for ``coords``.

    Returns an ``(n, n)`` array of kilometres with a zero diagonal. Vectorising
    matters here: the optimiser is called with up to a few hundred candidate
    places, and a Python double loop dominates the runtime at that size.
    """
    if not coords:
        return np.zeros((0, 0), dtype=float)

    lats = np.radians(np.array([c.lat for c in coords], dtype=float))
    lngs = np.radians(np.array([c.lng for c in coords], dtype=float))

    dlat = lats[:, None] - lats[None, :]
    dlng = lngs[:, None] - lngs[None, :]

    h = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(lats)[:, None] * np.cos(lats)[None, :] * np.sin(dlng / 2.0) ** 2
    )
    matrix = 2.0 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0)))
    np.fill_diagonal(matrix, 0.0)
    return matrix


def road_distance_matrix_km(
    coords: list[Coordinate], factor: float | None = None
) -> np.ndarray:
    """Great-circle distances inflated to approximate street distance."""
    factor = settings.road_distance_factor if factor is None else factor
    return haversine_matrix_km(coords) * factor


def travel_minutes(distance_km: float, mode: TransportMode) -> int:
    """Travel time for a leg, rounded up to whole minutes."""
    speed = MODE_SPEED_KMH.get(mode, settings.default_travel_speed_kmh)
    overhead = MODE_OVERHEAD_MINUTES.get(mode, 0)
    if distance_km <= 0:
        return 0
    return math.ceil((distance_km / speed) * 60.0) + overhead


def build_travel_matrix(
    coords: list[Coordinate], mode: TransportMode = TransportMode.WALKING
) -> tuple[np.ndarray, np.ndarray]:
    """Build the distance and duration matrices the optimiser consumes.

    Args:
        coords: Node coordinates. Index 0 is conventionally the day's origin.
        mode: Transport mode used for every leg.

    Returns:
        ``(distance_km, duration_minutes)``, both ``(n, n)``. Durations are
        integers because OR-Tools requires integral arc costs.
    """
    distances = road_distance_matrix_km(coords)
    if distances.size == 0:
        return distances, distances.astype(int)

    speed = MODE_SPEED_KMH.get(mode, settings.default_travel_speed_kmh)
    overhead = MODE_OVERHEAD_MINUTES.get(mode, 0)

    durations = np.ceil((distances / speed) * 60.0) + overhead
    # A node costs nothing to "travel" to from itself.
    np.fill_diagonal(durations, 0.0)
    return distances, durations.astype(int)


def effective_mode(distance_km: float, preferred: TransportMode) -> TransportMode:
    """Pick a sensible mode for one leg.

    A walker asked to cross 6 km would spend 80 minutes on it, so long legs fall
    back to transit even when walking is the stated preference. Short legs go the
    other way: driving 400 m is slower than walking it once parking is counted.
    """
    if preferred is TransportMode.WALKING and distance_km > 2.5:
        return TransportMode.TRANSIT
    if preferred is TransportMode.DRIVING and distance_km < 0.6:
        return TransportMode.WALKING
    return preferred


def total_route_distance_km(coords: list[Coordinate], order: list[int]) -> float:
    """Sum the road distance of visiting ``coords`` in ``order``."""
    if len(order) < 2:
        return 0.0
    factor = settings.road_distance_factor
    total = 0.0
    for current, following in pairwise(order):
        total += haversine_km(coords[current], coords[following]) * factor
    return round(total, 3)
