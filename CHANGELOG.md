# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — branch `feat/compare-and-apply-routes`

### Upgrade notes — read before deploying

- **Groq deployments must set `GROQ_MODEL`.** There is no longer a built-in Groq
  model; in staging/production the API refuses to start on Groq without
  `GROQ_API_KEY` and `GROQ_MODEL` (locally it falls back to the stub).
- **Run `python -m scripts.reembed` once after deploying.** Stored vectors now
  record which embedder made them, and search only compares matching ones; vectors
  from before this release carry no record and are invisible to semantic search
  until re-embedded. It is idempotent and takes seconds with the default embedder.
- **Run `python -m scripts.fetch_city_photos` once per environment** for city
  photos. Without it, cities show their curated photo or a gradient, as before.
- `NEXT_PUBLIC_MAPBOX_TOKEN` and `MAPBOX_ACCESS_TOKEN` are gone (see Removed);
  a leftover value is ignored.

Every API change below is additive: new optional request fields, new response
fields with defaults, and a new endpoint. Existing clients keep working.

### Added

- **Compare routes** dialog in the day view: every algorithm on the same day,
  with travel, visit and stop totals, multiple winner badges, the current order
  drawn as a baseline, honest loading/error/timeout states, and cancellation.
- **Algorithm choice per trip** (`constraints.optimizer_algorithm`), with
  documented precedence: request → trip → `OPTIMIZER_ALGORITHM` → `auto`.
- **Apply a compared route**: `POST /itineraries/{id}/days/{n}/apply-route`
  reorders a day and recomputes times server-side. A stale comparison returns 409;
  the UI updates optimistically, rolls back on error and offers Undo.
- **Road routing** (`ROUTING_PROVIDER=openrouteservice|osrm`): one matrix call per
  request, a cache, retries with backoff and a circuit breaker. Any failure falls back
  to the estimate. Responses and itineraries say which via `estimated` and
  `routing_provider`, and the compare dialog shows it.
- **Embedding providers** (`EMBEDDING_PROVIDER=hash|gemini|openai|local`),
  separate from the LLM, with batching, retries and a resumable re-embed command.
- **City photos** from Wikimedia Commons, freely licensed only, with author and
  licence shown (linked on city pages) and a gradient fallback.
- `geo` / `vector` test markers; the smoke check covers apply-route, the
  algorithm setting, routing and photos.
- [`docs/deployment.md`](docs/deployment.md): Atlas + Render + Vercel, step by step.

### Changed

- Groq calls have an explicit timeout and bounded retries. A rate limit that
  outlasts them is a 429 saying how long to wait, and JSON replies use Groq's
  JSON mode (still validated).
- Every optimiser engine reads legs from one travel matrix, so all algorithms
  and their comparison are priced the same way.
- CI: mypy blocks merges, and the test job fails, rather than skips, if MongoDB
  is missing.

### Fixed

- `updated_at` never changed after creation, on any collection: Beanie's `save()`
  fires its own event, which the timestamp hook did not listen for.
- Changing the password of a Google/Apple-only account returned 500.
- Applied and reordered days priced legs as straight-line distance without the
  road factor, so their totals disagreed with the comparison that chose them.
- An embedding provider failure silently fell back to another embedder, mixing
  incomparable vectors in one index; vectors were also labelled with a model
  that had not produced them.
- The city page hero showed an unrelated stock photo (a lake, a mountain) for
  cities without their own.

### Removed

- The Mapbox map layer and its setup instructions. The layer pointed at a tile
  route that does not serve Mapbox, so setting the token as documented produced a
  blank map. Use `NEXT_PUBLIC_MAP_TILE_URL` for a hosted tile provider instead.
- The unused backend `MAPBOX_ACCESS_TOKEN` setting.
