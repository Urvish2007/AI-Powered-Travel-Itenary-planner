"""MongoDB connection lifecycle and Beanie initialisation.

This module replaces what SQLAlchemy's engine/session machinery would provide in
the PostgreSQL version of this stack. Beanie sits on top of Motor and gives us
Pydantic-native documents, so the models are the schema - there is no separate
mapping layer.
"""

from __future__ import annotations

import re
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

_SRV_PREFIX = "mongodb+srv://"
# Matches a trailing ":<digits>" at the end of a host section.
_TRAILING_PORT = re.compile(r":\d+$")


def normalize_mongo_uri(uri: str) -> str:
    """Return a URI that pymongo will accept.

    ``mongodb+srv://`` URIs must not contain a port: the host, port and replica
    set members come from DNS. Pydantic's ``MongoDsn`` type applies a default
    port of 27017 when it serialises a URI back to a string, which turns a valid
    Atlas SRV string into one pymongo rejects with
    ``InvalidURI: mongodb+srv:// URIs must not include a port number``.

    This strips that port for SRV URIs and leaves every other URI untouched.
    Credentials are never modified, so special characters in the password are
    safe.
    """
    uri = uri.strip().strip("\"'")  # tolerate quotes pasted into env vars

    if not uri.startswith(_SRV_PREFIX):
        return uri

    rest = uri[len(_SRV_PREFIX):]

    # The authority ends at the first "/" or "?" (a "/" or "?" cannot appear
    # unescaped in credentials, so this split is safe).
    end = len(rest)
    for sep in ("/", "?"):
        idx = rest.find(sep)
        if idx != -1:
            end = min(end, idx)
    authority, tail = rest[:end], rest[end:]

    # Split credentials from host on the LAST "@" (passwords may contain "@"
    # only when percent-encoded, but be defensive).
    if "@" in authority:
        creds, host = authority.rsplit("@", 1)
        creds += "@"
    else:
        creds, host = "", authority

    host = _TRAILING_PORT.sub("", host)
    return f"{_SRV_PREFIX}{creds}{host}{tail}"


def _redact(uri: str) -> str:
    """Hide credentials so a URI is safe to log."""
    return re.sub(r"//[^@/]+@", "//***:***@", uri)


async def connect_to_mongo(
    uri: str | None = None, db_name: str | None = None
) -> AsyncIOMotorDatabase:
    """Open the Motor client and register every Beanie document.

    Safe to call more than once: a second call reuses the existing client.
    If startup fails part-way, the half-open client is closed so a retry starts
    clean.
    """
    global _client, _database

    if _database is not None:
        return _database

    raw_uri = uri or settings.mongodb_uri_str
    uri = normalize_mongo_uri(str(raw_uri))
    db_name = db_name or settings.mongodb_db_name

    logger.info("mongodb_connecting", uri=_redact(uri), database=db_name)

    client = AsyncIOMotorClient(
        uri,
        uuidRepresentation="standard",
        serverSelectionTimeoutMS=5_000,
        connectTimeoutMS=5_000,
        maxPoolSize=50,
        minPoolSize=5,
        retryWrites=True,
    )
    database = client[db_name]

    try:
        await init_beanie(database=database, document_models=ALL_DOCUMENTS)
    except Exception:
        # Do not leave a dangling client/pool behind on a failed startup.
        client.close()
        DB_UP.set(0)
        raise

    _client, _database = client, database

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
    target = db_name or settings.mongodb_db_name
    await client.drop_database(target)
    logger.warning("mongodb_database_dropped", database=target)
