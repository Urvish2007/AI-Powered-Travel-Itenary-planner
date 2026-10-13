"""Seed the database.

Usage (run from the ``backend/`` directory):
    python -m scripts.seed_db             # users, the core places and the world places
    python -m scripts.seed_db --reset     # drop the database first
    python -m scripts.seed_db --no-embed  # skip embedding generation
    python -m scripts.seed_db --no-world  # only the hand-written Paris/Rome/Tokyo set

Running ``python scripts/seed_db.py`` also works: the backend directory is added
to ``sys.path`` below so ``import app`` resolves either way.

The world corpus adds 2,000 places across 100 cities (see app/db/world_seed.py).
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

# Make ``app`` importable when this file is executed directly (not via -m).
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.db.indexes import ensure_vector_search_index  # noqa: E402
from app.db.mongodb import (  # noqa: E402
    close_mongo_connection,
    connect_to_mongo,
    drop_database,
)
from app.db.seed import seed_all  # noqa: E402
from app.db.world_seed import seed_world_places  # noqa: E402
from app.observability.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


def _redact(uri: str) -> str:
    """Hide credentials so a connection string is safe to print."""
    return re.sub(r"//[^@/]+@", "//***:***@", str(uri))


async def main(reset: bool, embed: bool, world: bool) -> int:
    configure_logging(level=settings.log_level)
    await connect_to_mongo()

    try:
        if reset:
            if settings.is_production:
                print("Refusing to reset a production database.", file=sys.stderr)
                return 2
            await drop_database()
            # Dropping removes the collections, so Beanie has to re-register them.
            await close_mongo_connection()
            await connect_to_mongo()

        await ensure_vector_search_index()
        counts = await seed_all(embed=embed)
        world_count = await seed_world_places(embed=embed) if world else 0

        print(f"Seeded {counts['users']} user(s) and {counts['places']} place(s).")
        if world:
            print(f"Seeded {world_count} world place(s) across 100 cities.")
        print(
            f"Database: {settings.mongodb_db_name} "
            f"at {_redact(settings.mongodb_uri_str)}"
        )
        if counts["users"]:
            print("\nDemo accounts (change or delete these on any public deployment):")
            print("  demo@example.com  / demopassword1   (traveller)")
            print("  admin@example.com / adminpassword1  (admin)")
        return 0
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the itinerary planner database.")
    parser.add_argument(
        "--reset", action="store_true", help="Drop the database before seeding."
    )
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help="Skip embedding generation (faster, but semantic search will be empty).",
    )
    parser.add_argument(
        "--no-world",
        action="store_true",
        help="Skip the 2,000-place world corpus and seed only Paris, Rome and Tokyo.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.reset, not args.no_embed, not args.no_world)))
