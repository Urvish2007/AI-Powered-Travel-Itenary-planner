"""Check the world places corpus for structural and geographic problems.

Parses every file (so format errors surface with file:line), then measures each
place's distance from its city centre. A place far from its city is usually a
typo - a dropped minus sign, swapped digits - so outliers are listed for review.

Usage:
    python -m scripts.validate_world_data              # report, fail on hard errors
    python -m scripts.validate_world_data --radius 30  # tighter outlier threshold
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from app.db.world_seed import ALLOWED_FAR, WorldDataError, load_cities, parse_world_places
from app.optimization.distance import Coordinate, haversine_km


def main(radius_km: float, hard_limit_km: float) -> int:
    try:
        rows = parse_world_places()
    except WorldDataError as exc:
        print(f"FORMAT ERROR: {exc}", file=sys.stderr)
        return 1

    cities = load_cities()
    per_city = Counter(r["city"] for r in rows)
    far: list[tuple[float, dict]] = []
    fatal = False

    for row in rows:
        city = cities[row["city"]]
        km = haversine_km(
            Coordinate(city.latitude, city.longitude), Coordinate(row["lat"], row["lng"])
        )
        if km > radius_km and (row["city"], row["name"]) not in ALLOWED_FAR:
            far.append((km, row))
        if km > hard_limit_km and (row["city"], row["name"]) not in ALLOWED_FAR:
            fatal = True

    print(f"{len(rows)} places in {len(per_city)} cities")
    uneven = {c: n for c, n in per_city.items() if n != 20}
    if uneven:
        print(f"Cities without exactly 20 places: {uneven}")

    if far:
        print(f"\nPlaces more than {radius_km:g} km from their city centre:")
        for km, row in sorted(far, key=lambda item: -item[0]):
            print(
                f"  {km:6.1f} km  {row['city']:<18} {row['name']}"
                f"  ({row['lat']}, {row['lng']})"
            )

    return 1 if fatal else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--radius", type=float, default=25.0, help="report places beyond this many km"
    )
    parser.add_argument(
        "--limit", type=float, default=65.0, help="fail on places beyond this many km"
    )
    args = parser.parse_args()
    raise SystemExit(main(args.radius, args.limit))
