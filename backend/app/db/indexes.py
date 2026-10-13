"""Index management beyond what Beanie declares on the documents.

Beanie creates each document's ``Settings.indexes`` automatically at init time.
What lives here is the Atlas Vector Search index, which is not a normal MongoDB
index: it is created through the ``createSearchIndexes`` command and only exists
on Atlas (or a local deployment running Atlas Search). On a plain ``mongod`` the
call fails, and the retrieval layer falls back to in-process cosine similarity.

This is the MongoDB counterpart of creating a pgvector ``ivfflat`` index.
"""

from __future__ import annotations

from typing import Any

from pymongo.errors import OperationFailure, PyMongoError

from app.core.config import settings
from app.db.mongodb import get_database
from app.observability.logging import get_logger

logger = get_logger(__name__)


def vector_index_definition() -> dict[str, Any]:
    """The Atlas Vector Search index definition for ``places.embedding``."""
    return {
        "name": settings.mongodb_vector_index_name,
        "type": "vectorSearch",
        "definition": {
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": settings.embedding_dimensions,
                    "similarity": "cosine",
                },
                # Filter fields let a vector query be scoped to a city or category
                # without a second round trip.
                {"type": "filter", "path": "city"},
                {"type": "filter", "path": "category"},
                {"type": "filter", "path": "is_active"},
            ]
        },
    }


async def ensure_vector_search_index() -> bool:
    """Create the Atlas Vector Search index when possible.

    Returns:
        True when the index exists (or was just created), False when the
        deployment does not support Atlas Search.
    """
    if not settings.mongodb_vector_search_enabled:
        logger.info("vector_index_skipped", reason="disabled_in_settings")
        return False

    database = get_database()
    try:
        existing = await database["places"].list_search_indexes().to_list(length=None)
        names = {index.get("name") for index in existing}
        if settings.mongodb_vector_index_name in names:
            logger.info("vector_index_present", name=settings.mongodb_vector_index_name)
            return True

        await database.command(
            {
                "createSearchIndexes": "places",
                "indexes": [vector_index_definition()],
            }
        )
    except (OperationFailure, PyMongoError) as exc:
        logger.warning(
            "vector_index_unavailable",
            error=str(exc),
            hint="Atlas Vector Search requires MongoDB Atlas; using cosine fallback.",
        )
        return False

    logger.info("vector_index_created", name=settings.mongodb_vector_index_name)
    return True


async def list_indexes(collection: str) -> list[dict[str, Any]]:
    """Return the regular indexes on ``collection``, for diagnostics."""
    database = get_database()
    return await database[collection].list_indexes().to_list(length=None)


async def index_report() -> dict[str, list[str]]:
    """Map each collection to its index names. Used by the admin health endpoint."""
    database = get_database()
    report: dict[str, list[str]] = {}
    for name in await database.list_collection_names():
        try:
            indexes = await database[name].list_indexes().to_list(length=None)
        except PyMongoError as exc:
            logger.warning("index_listing_failed", collection=name, error=str(exc))
            continue
        report[name] = [index["name"] for index in indexes]
    return report
