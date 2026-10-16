"""MongoDB connection lifecycle and Beanie initialisation.

This module replaces what SQLAlchemy's engine/session machinery would provide in
the PostgreSQL version of this stack. Beanie sits on top of Motor and gives us
Pydantic-native documents, so the models are the schema - there is no separate
mapping layer.
"""

from __future__ import annotations

from typing import Any

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from app.core.config import settings
from app.models import ALL_DOCUMENTS
from app.observability.logging import get_logger
from app.observability.metrics import DB_UP

logger = get_logger(__name__)

_client: AsyncIOMotorClient | None = None
_database: AsyncIOMotorDatabase | None = None


async def connect_to_mongo(
    uri: str | None = None, db_name: str | None = None
) -> AsyncIOMotorDatabase:
    """Open the Motor client and register every Beanie document.

    Safe to call more than once: a second call reuses the existing client.
    """
    global _client, _database

    if _database is not None:
        return _database

    uri = uri or settings.mongodb_uri_str
    db_name = db_name or settings.mongodb_db_name

    try:
        _client = AsyncIOMotorClient(
            uri,
            uuidRepresentation="standard",
            serverSelectionTimeoutMS=5_000,
            connectTimeoutMS=5_000,
            maxPoolSize=50,
            minPoolSize=5,
            retryWrites=True,
        )
    except PyMongoError as exc:
        _hint = ""
        if "mongodb+srv" in (uri or ""):
            _hint = (
                " mongodb+srv:// URIs require the 'dnspython' package. "
                "Install it with: pip install dnspython"
            )
        raise RuntimeError(f"Failed to create MongoDB client: {exc}.{_hint}") from exc

    _database = _client[db_name]

    await init_beanie(database=_database, document_models=ALL_DOCUMENTS)

    logger.info("mongodb_connected", database=db_name)
    DB_UP.set(1)
    return _database


async def close_mongo_connection() -> None:
    """Close the client and reset module state."""
    global _client, _database
    if _client is not None:
        _client.close()
        logger.info("mongodb_disconnected")
    _client = None
    _database = None
    DB_UP.set(0)


def get_database() -> AsyncIOMotorDatabase:
    """Return the live database handle.

    Raises:
        RuntimeError: when called before ``connect_to_mongo``.
    """
    if _database is None:
        raise RuntimeError(
            "MongoDB is not connected; call connect_to_mongo() during startup."
        )
    return _database


def get_client() -> AsyncIOMotorClient:
    if _client is None:
        raise RuntimeError(
            "MongoDB is not connected; call connect_to_mongo() during startup."
        )
    return _client


async def ping() -> bool:
    """Return True when the server answers a ping, updating the ``mongodb_up`` gauge."""
    try:
        client = get_client()
        await client.admin.command("ping")
    except (PyMongoError, RuntimeError) as exc:
        logger.warning("mongodb_ping_failed", error=str(exc))
        DB_UP.set(0)
        return False
    DB_UP.set(1)
    return True


async def server_info() -> dict[str, Any]:
    """Version and topology details, for the detailed health endpoint."""
    client = get_client()
    info = await client.admin.command("buildInfo")
    return {
        "version": info.get("version"),
        "git_version": info.get("gitVersion"),
        "modules": info.get("modules", []),
    }


async def drop_database(db_name: str | None = None) -> None:
    """Drop a database outright. Intended for test teardown only."""
    if settings.is_production:
        raise RuntimeError("refusing to drop a database in production")
    client = get_client()
    await client.drop_database(db_name or settings.mongodb_db_name)
    logger.warning("mongodb_database_dropped", database=db_name or settings.mongodb_db_name)
