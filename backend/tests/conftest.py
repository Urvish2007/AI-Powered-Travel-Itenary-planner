"""Shared test fixtures.

The suite runs against a **real MongoDB** on a throwaway database rather than a
mock. Geospatial queries, text indexes and TTL behaviour are the parts most
likely to break, and a mock would assert nothing about any of them.

Set ``TEST_MONGODB_URI`` to point at a different server. If none is reachable,
the database-backed tests are skipped rather than failing, so the pure-unit
tests still run in an environment without MongoDB.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("LLM_PROVIDER", "stub")
os.environ.setdefault("LOG_LEVEL", "WARNING")

TEST_MONGODB_URI = os.getenv("TEST_MONGODB_URI", "mongodb://127.0.0.1:27017")
TEST_DB_NAME = f"itinerary_planner_test_{uuid.uuid4().hex[:8]}"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "db: requires a reachable MongoDB server")
    # Both run on any MongoDB the db tests use (CI's mongo:8 service container
    # included). Marked so they can be selected or skipped as a group, e.g.
    # `pytest -m "not vector"` on a deployment whose search is being changed.
    config.addinivalue_line(
        "markers", "geo: exercises 2dsphere queries ($near, radius search)"
    )
    config.addinivalue_line(
        "markers",
        "vector: exercises semantic search (Atlas $vectorSearch when enabled, "
        "otherwise the in-process cosine fallback)",
    )


@pytest.fixture(scope="session")
def mongodb_available() -> bool:
    """Probe the server once per session.

    Synchronous on purpose: a session-scoped *async* fixture would need a
    session-scoped event loop, which pytest-asyncio refuses to hand to
    function-scoped tests.
    """
    import pymongo

    try:
        client = pymongo.MongoClient(TEST_MONGODB_URI, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        client.close()
    except Exception:
        return False
    return True


@pytest_asyncio.fixture(scope="function")
async def database(mongodb_available: bool) -> AsyncIterator[Any]:
    """A clean database per test.

    Function scope keeps tests independent: a failure in one cannot leave state
    that makes the next one pass or fail spuriously.
    """
    if not mongodb_available:
        # Locally, skipping keeps the unit tests usable without a server. In CI
        # a skip would turn a broken service container into a green run that
        # tested almost nothing, so REQUIRE_MONGODB makes it a failure.
        if os.getenv("REQUIRE_MONGODB") == "1":
            pytest.fail(f"REQUIRE_MONGODB=1 but no MongoDB at {TEST_MONGODB_URI}")
        pytest.skip(f"no MongoDB at {TEST_MONGODB_URI}")

    from app.db import mongodb as mongo_module

    db_name = f"{TEST_DB_NAME}_{uuid.uuid4().hex[:6]}"
    db = await mongo_module.connect_to_mongo(TEST_MONGODB_URI, db_name)
    try:
        yield db
    finally:
        client = mongo_module.get_client()
        await client.drop_database(db_name)
        await mongo_module.close_mongo_connection()


@pytest_asyncio.fixture
async def client(database: Any) -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to the app in-process.

    The app is built without its lifespan so the test's database fixture stays in
    control of the connection; ASGITransport means no socket is ever opened.
    """
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def seeded(database: Any) -> dict[str, int]:
    from app.db.seed import seed_all

    return await seed_all(embed=True)


@pytest_asyncio.fixture
async def user_token(client: AsyncClient) -> dict[str, str]:
    """Register a fresh user and return their Authorization header."""
    email = f"test-{uuid.uuid4().hex[:8]}@example.com"
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "testPassword123",
            "full_name": "Test User",
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["tokens"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def admin_token(client: AsyncClient, seeded: dict[str, int]) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "adminpassword1"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["tokens"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def paris_trip_payload() -> dict[str, Any]:
    start = date.today() + timedelta(days=21)
    return {
        "title": "Test trip to Paris",
        "destination": {
            "city": "Paris",
            "country": "France",
            "latitude": 48.8566,
            "longitude": 2.3522,
            "timezone": "Europe/Paris",
        },
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=2)).isoformat(),
        "party": {"adults": 2, "children": 0, "infants": 0},
        "constraints": {
            "pace": "moderate",
            "budget_level": "moderate",
            "currency": "EUR",
            "interests": ["history", "art"],
            "preferred_transport": "walking",
            "day_start_time": "09:00",
            "day_end_time": "20:00",
            "max_daily_walking_km": 10.0,
        },
    }
