"""Re-embed places into the configured embedding space.

Run after changing EMBEDDING_PROVIDER, EMBEDDING_MODEL or EMBEDDING_DIMENSIONS:
search only compares vectors from the configured space, so until this runs the
places embedded the old way are invisible to semantic search.

Idempotent and resumable: it only touches places not already in the current
space, so running it twice does nothing the second time, and a run stopped by a
rate limit carries on where it left off when run again.

Usage (from the backend/ directory):
    python -m scripts.reembed              # embed everything that needs it
    python -m scripts.reembed --dry-run    # just count
    python -m scripts.reembed --limit 500  # at most 500 places this run

Exit codes: 0 finished, 1 error, 2 stopped early (rate limit / provider down).
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

from app.ai.embeddings import EmbeddingUnavailableError  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.indexes import ensure_vector_search_index  # noqa: E402
from app.db.mongodb import close_mongo_connection, connect_to_mongo  # noqa: E402
from app.observability.logging import configure_logging  # noqa: E402
from app.services.place_service import reembed_places  # noqa: E402


async def main(batch_size: int, limit: int | None, dry_run: bool) -> int:
    configure_logging(level=settings.log_level)
    await connect_to_mongo()
    try:
        report = await reembed_places(batch_size=batch_size, limit=limit, dry_run=dry_run)
        if dry_run:
            print(f"{report.remaining} places need embedding into {report.space}.")
            return 0
        # The Atlas index must match the space's dimension; keep it in step.
        await ensure_vector_search_index()
    except EmbeddingUnavailableError as exc:
        print(f"Cannot embed: {exc.message}", file=sys.stderr)
        return 1
    finally:
        await close_mongo_connection()

    print(
        f"Embedded {report.written} places into {report.space}; {report.remaining} remain."
    )
    if report.stopped is not None:
        print(f"Stopped early: {report.stopped} Run again to continue.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.batch_size, args.limit, args.dry_run)))
