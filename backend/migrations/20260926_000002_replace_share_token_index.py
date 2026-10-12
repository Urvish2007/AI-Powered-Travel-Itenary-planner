"""Replace the sparse unique index on ``trips.share_token`` with a partial one.

**Why this migration has to exist.** Beanie creates a document's declared
indexes at startup, but MongoDB will not *redefine* an index that already
exists under the same name — it raises ``IndexKeySpecsConflict`` and the
application fails to boot. Changing an index definition is therefore a two-step
operation: drop, then let Beanie recreate. That is a data migration, not
something the ODM can do for you.

**What was wrong with the old index.** It was declared ``sparse=True``, which
skips documents where the field is *absent*. Beanie always writes the field, so
an unshared trip stores ``share_token: null`` explicitly — and a sparse unique
index happily indexes those nulls. The result: a user could create one trip, and
the second failed with a duplicate key error. The partial index replacing it
covers only documents where ``share_token`` is a string, so unshared trips are
not indexed at all.

Safe to run against a database that never had the old index: dropping is
guarded, and the forward step is then a no-op.
"""

import sys
from pathlib import Path

from beanie import free_fall_migration
from pymongo.errors import OperationFailure

# The beanie CLI execs migration modules without setting ``__file__`` and
# without the project root on sys.path. The working directory is the anchor
# instead - see the sibling migration for the full explanation.
_PROJECT_ROOT = str(Path.cwd())
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

INDEX_NAME = "uniq_share_token"


async def _drop_index(session) -> bool:
    """Drop the index if present. Returns True when something was dropped."""
    from app.core.config import settings

    collection = session.client[settings.mongodb_db_name]["trips"]
    existing = {index["name"] async for index in collection.list_indexes()}
    if INDEX_NAME not in existing:
        return False
    try:
        await collection.drop_index(INDEX_NAME)
    except OperationFailure:
        # Another replica may have dropped it between the check and the call.
        return False
    return True


class Forward:
    @free_fall_migration(document_models=[])
    async def replace_with_partial_index(self, session) -> None:
        """Drop the old index and build the partial one.

        The index is created explicitly here rather than left to the next
        application start, so that a deployment which runs migrations before
        booting does not serve traffic with no uniqueness guarantee at all.
        """
        from app.core.config import settings

        await _drop_index(session)

        collection = session.client[settings.mongodb_db_name]["trips"]
        await collection.create_index(
            [("share_token", 1)],
            name=INDEX_NAME,
            unique=True,
            partialFilterExpression={"share_token": {"$type": "string"}},
        )


class Backward:
    @free_fall_migration(document_models=[])
    async def restore_sparse_index(self, session) -> None:
        """Put the sparse index back.

        Reversible, but note that rolling back reinstates the bug: with more
        than one unshared trip in the collection the sparse unique index cannot
        be built at all, and this step will fail. That is the correct outcome —
        it surfaces the conflict rather than silently leaving the database in a
        state the old code cannot use.
        """
        from app.core.config import settings

        await _drop_index(session)

        collection = session.client[settings.mongodb_db_name]["trips"]
        await collection.create_index(
            [("share_token", 1)], name=INDEX_NAME, unique=True, sparse=True
        )
