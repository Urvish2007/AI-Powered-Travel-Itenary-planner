"""Regenerate ``app/db/world_data/cities.json`` from the ``=City|CC`` headers.

Developer tool: it needs ``geonamescache`` (``pip install geonamescache``), which
is deliberately *not* a runtime dependency - the JSON it writes is committed, so
the API and the seed script never import GeoNames.

Header forms understood in ``world_data/*.txt``::

    =Barcelona|ES                                   # looked up by name
    =Reykjavik|IS|Reykjavík                         # looked up under another spelling
    =Queenstown|NZ|@-45.0312,168.6626,Pacific/Auckland   # not in GeoNames: set by hand

Usage:
    python -m scripts.build_world_cities
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "app" / "db" / "world_data"
OUTPUT = DATA_DIR / "cities.json"


def _headers() -> list[tuple[str, str, str | None, str]]:
    """``(display name, country code, extra, source file)`` for every header."""
    found: list[tuple[str, str, str | None, str]] = []
    for path in sorted(DATA_DIR.glob("*.txt")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line.startswith("="):
                continue
            parts = [p.strip() for p in line[1:].split("|")]
            if len(parts) < 2:
                raise SystemExit(f"{path.name}: bad header {line!r}")
            found.append(
                (
                    parts[0],
                    parts[1].upper(),
                    parts[2] if len(parts) > 2 else None,
                    path.name,
                )
            )
    return found


def main() -> int:
    try:
        import geonamescache
    except ImportError:
        print("Install geonamescache first: pip install geonamescache", file=sys.stderr)
        return 2

    cache = geonamescache.GeonamesCache()
    all_cities = list(cache.get_cities().values())
    countries = cache.get_countries()

    resolved: dict[str, dict[str, object]] = {}
    problems: list[str] = []

    for name, code, extra, source in _headers():
        if name in resolved:
            problems.append(f"{source}: city {name!r} is defined twice")
            continue
        if code not in countries:
            problems.append(f"{source}: unknown country code {code!r} for {name!r}")
            continue
        country = countries[code]["name"]

        if extra and extra.startswith("@"):
            try:
                lat_s, lng_s, tz = extra[1:].split(",")
                lat, lng = float(lat_s), float(lng_s)
            except ValueError:
                problems.append(f"{source}: bad manual entry for {name!r}: {extra!r}")
                continue
            resolved[name] = {
                "country": country,
                "country_code": code,
                "timezone": tz,
                "latitude": lat,
                "longitude": lng,
                "manual": True,
            }
            continue

        wanted = (extra or name).casefold()
        matches = [
            c
            for c in all_cities
            if c["countrycode"] == code
            and (
                c["name"].casefold() == wanted
                or wanted in (a.casefold() for a in c["alternatenames"])
            )
        ]
        if not matches:
            problems.append(f"{source}: {name!r} ({code}) not found in GeoNames")
            continue

        best = max(matches, key=lambda c: c["population"])
        resolved[name] = {
            "country": country,
            "country_code": code,
            "timezone": best["timezone"],
            "latitude": round(best["latitude"], 5),
            "longitude": round(best["longitude"], 5),
        }

    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1

    OUTPUT.write_text(
        json.dumps(dict(sorted(resolved.items())), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(resolved)} cities to {OUTPUT.relative_to(OUTPUT.parents[3])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
