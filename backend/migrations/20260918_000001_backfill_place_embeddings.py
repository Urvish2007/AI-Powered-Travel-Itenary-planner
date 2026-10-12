"""Backfill embeddings for places that predate the vector-search feature.

This is the Alembic replacement. Run with:

    beanie migrate --no-use-transaction \
        -uri "mongodb://localhost:27017" -db itinerary_planner -p migrations

``--no-use-transaction`` is required against a standalone ``mongod``; drop it
when running against a replica set (which is what docker-compose starts).

Beanie creates *indexes* automatically at startup from each document's
``Settings.indexes``, so migrations here are for **data**, not schema:
backfills, field renames and re-derivations. That is the practical difference
from a relational stack, where the schema change itself is the migration.

A migration module defines a ``Forward`` and a ``Backward`` class; Beanie
records which files have run, so re-running is a no-op.
"""

import sys
from pathlib import Path

from beanie import free_fall_migration

# The beanie CLI execs each migration module without setting ``__file__`` and
# without the project root on sys.path, so ``app`` is not importable by default.
# The working directory is the anchor instead: the documented command passes
# ``-p migrations`` as a relative path, so it can only succeed when run from the
# backend directory in the first place.
_PROJECT_ROOT = str(Path.cwd())
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class Forward:
    @free_fall_migration(document_models=[])
    async def backfill_embeddings(self, session) -> None:
        """Embed every active place that has no embedding yet.

        Written against the raw collection rather than the Place document so the
        migration keeps working if the model later gains required fields that
        old documents do not have.
        """
        from app.ai.embeddings import embed_text, place_embedding_text
        from app.core.config import settings

        database = session.client[settings.mongodb_db_name]
        collection = database["places"]

        cursor = collection.find(
            {"$or": [{"embedding": None}, {"embedding": {"$exists": False}}]}
        )
        async for document in cursor:
            text = place_embedding_text(
                document.get("name", ""),
                document.get("description", ""),
                str(document.get("category", "other")),
                [str(t) for t in document.get("tags", [])],
                document.get("city", ""),
            )
            await collection.update_one(
                {"_id": document["_id"]},
                {
                    "$set": {
                        "embedding": await embed_text(text),
                        "embedding_model": settings.embedding_model,
                    }
                },
            )


class Backward:
    @free_fall_migration(document_models=[])
    async def clear_embeddings(self, session) -> None:
        """Drop the embeddings again.

        Reversible because embeddings are derived data: nothing is lost that
        cannot be recomputed by running the forward migration.
        """
        from app.core.config import settings

        database = session.client[settings.mongodb_db_name]
        await database["places"].update_many(
            {}, {"$set": {"embedding": None, "embedding_model": None}}
        )
