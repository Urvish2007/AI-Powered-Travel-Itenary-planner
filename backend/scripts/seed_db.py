"""Seed the database.

Usage:
    python -m scripts.seed_db             # users, the core places and the world places
    python -m scripts.seed_db --reset     # drop the database first
    python -m scripts.seed_db --no-embed  # skip embedding generation
    python -m scripts.seed_db --no-world  # only the hand-written Paris/Rome/Tokyo set

The world corpus adds 2,000 places across 100 cities (see app/db/world_seed.py).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import settings
from app.db.indexes import ensure_vector_search_index
from app.db.mongodb import close_mongo_connection, connect_to_mongo, drop_database
from app.db.seed import seed_all
from app.db.world_seed import seed_world_places
from app.observability.logging import configure_logging, get_logger

logger = get_logger(__name__)


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
        print(f"Database: {settings.mongodb_db_name} at {settings.mongodb_uri_str}")
        if counts["users"]:
            print("\nDemo accounts:")
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
