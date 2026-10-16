"""Find a freely licensed photo for every city, from Wikimedia Commons.

Resumable: every city checked is recorded (found or not), so a rerun only asks
about cities never checked. Rate-limit aware: one request at a time, and a run
that Wikimedia keeps refusing stops with its counts instead of failing.

Usage (from the backend/ directory):
    python -m scripts.fetch_city_photos                  # cities not yet checked
    python -m scripts.fetch_city_photos --retry-missing  # also retry "none" cities
    python -m scripts.fetch_city_photos --refresh        # re-check every city
    python -m scripts.fetch_city_photos --limit 10       # at most 10 lookups

Exit codes: 0 finished, 2 stopped early (rate limit / outage; run again).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
_env_file = Path(_BACKEND_DIR) / ".env"
if _env_file.is_file() and str(Path.cwd()) != _BACKEND_DIR:
    os.chdir(_BACKEND_DIR)

from app.core.config import settings  # noqa: E402
from app.db.mongodb import close_mongo_connection, connect_to_mongo  # noqa: E402
from app.observability.logging import configure_logging  # noqa: E402
from app.services.photo_service import CityRef, fetch_city_photos  # noqa: E402
from app.services.place_service import city_summary  # noqa: E402


async def main(refresh: bool, retry_missing: bool, limit: int | None) -> int:
    configure_logging(level=settings.log_level)
    await connect_to_mongo()
    try:
        cities = [
            CityRef(c["city"], c["country"], c.get("latitude"), c.get("longitude"))
            for c in await city_summary()
        ]
        report = await fetch_city_photos(
            cities, refresh=refresh, retry_missing=retry_missing, limit=limit
        )
    finally:
        await close_mongo_connection()

    print(
        f"{len(cities)} cities: {report.found} photos found, {report.none} without a "
        f"usable photo, {report.skipped} already checked, {report.failed} failed."
    )
    for reason, count in sorted(report.reasons.items(), key=lambda item: -item[1]):
        print(f"  none - {reason}: {count}")
    if report.stopped is not None:
        print(f"Stopped early: {report.stopped}. Run again to continue.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--retry-missing", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.refresh, args.retry_missing, args.limit)))
