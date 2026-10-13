"""Seed the database.

Usage (from the backend/ directory):
    python -m scripts.seed_db             # users, the core places and the world places
    python -m scripts.seed_db --reset     # drop the database first
    python -m scripts.seed_db --no-embed  # skip embedding generation
    python -m scripts.seed_db --no-world  # only the hand-written Paris/Rome/Tokyo set

Or run directly from VS Code (F5 / right-click → Run):
    python scripts/seed_db.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# ── sys.path fixup ──────────────────────────────────────────────────────────
# When VS Code launches this script directly (F5 or the integrated terminal
# with a CWD other than backend/), `app` is not on sys.path. Detect the
# backend root relative to this file and inject it.
_BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# ── .env loading ────────────────────────────────────────────────────────────
# pydantic-settings reads .env from the CWD, which may differ from backend/.
# Nudge the CWD so the settings singleton picks up the right file.
_env_file = Path(_BACKEND_DIR) / ".env"
if _env_file.is_file() and str(Path.cwd()) != _BACKEND_DIR:
    os.chdir(_BACKEND_DIR)

from app.core.config import settings  # noqa: E402
from app.db.indexes import ensure_vector_search_index  # noqa: E402
from app.db.mongodb import close_mongo_connection, connect_to_mongo, drop_database  # noqa: E402
from app.db.seed import seed_all  # noqa: E402
from app.db.world_seed import seed_world_places  # noqa: E402
from app.observability.logging import configure_logging, get_logger  # noqa: E402

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
