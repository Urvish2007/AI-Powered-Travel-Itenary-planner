"""Aggregates every v1 endpoint module into one router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import ai, auth, health, itineraries, places, trips, users

api_router = APIRouter()

# Health first, so its unprefixed paths are not shadowed by a resource prefix.
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(places.router)
api_router.include_router(trips.router)
api_router.include_router(itineraries.router)
api_router.include_router(ai.router)
