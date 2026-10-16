# AI-Powered Travel Itinerary Planner

An LLM decides **what is worth doing**; Google OR-Tools decides **when and in what order**.

| Part | Stack | Docs |
|---|---|---|
| `backend/` | FastAPI, MongoDB (Beanie), OR-Tools, Celery + Redis | [backend/README.md](backend/README.md) |
| `frontend/` | Next.js 16, React 19, TanStack Query, Leaflet | — |

## Quick start (local)

```bash
# 1. Backend + MongoDB + Redis + worker (runs migrations automatically)
cd backend
cp .env.example .env            # then set SECRET_KEY (see below)
docker compose up --build -d
docker compose exec api python -m scripts.seed_db --reset
curl http://localhost:8000/api/v1/health/ready

# 2. Frontend
cd ../frontend
cp .env.example .env.local      # works as-is; no map key needed
npm ci
npm run dev                     # http://localhost:3000
```

API docs: <http://localhost:8000/docs> (disabled when `ENVIRONMENT=production`).
Demo logins after seeding: `demo@example.com` / `demopassword1` and
`admin@example.com` / `adminpassword1`. **Never seed these into a real environment.**

Generate a secret: `python -c "import secrets; print(secrets.token_urlsafe(48))"`

## AI provider keys

The app runs with **no key** on a deterministic stub. For real plans, set one:

| `LLM_PROVIDER` | Key variable | Default model |
|---|---|---|
| `gemini` | `GOOGLE_GEMINI_KEY` | `gemini-3.8-flash` |
| `groq` | `GROQ_API_KEY` | `openai/gpt-oss-120b` |

Override with `LLM_MODEL` / `LLM_FAST_MODEL`. Providers retire models on a
schedule, so re-check their model pages before pinning one. If the key for the
chosen provider is missing, the API logs a warning and falls back to the stub.

## Database

- **Local:** the compose file starts a single-node MongoDB replica set.
- **Staging / production:** use MongoDB Atlas. Create a user with `readWrite` on
  one database only, restrict network access, and enable backups. Set
  `MONGODB_VECTOR_SEARCH_ENABLED=true` to use Atlas Vector Search (the app creates
  the index at startup); check Atlas's current limits for your tier.
- **Migrations** (data backfills and index changes) run with
  `sh backend/scripts/migrate.sh`. Compose runs it before the API starts, CI runs it
  in the smoke test, and the deploy pipeline runs it before each rollout. Each
  migration must stay compatible with the previous app version, because it runs
  before the new version starts.

## Place data

`python -m scripts.seed_db` loads two sets:

| Set | Size | Source |
|---|---|---|
| Core | 37 places across Paris, Rome and Tokyo | hand-written, `app/db/seed.py` |
| World | 2,000 places across 100 cities | `app/db/world_data/*.txt`, loaded by `app/db/world_seed.py` |

```bash
python -m scripts.seed_db --reset       # drop, then seed both sets
python -m scripts.seed_db --no-world    # core set only
python -m scripts.seed_db --no-embed    # skip embeddings (see the warning below)
python -m scripts.validate_world_data   # structural + geographic checks
```

With `LLM_PROVIDER=openai`, embedding 2,000 places means 2,000 API calls. Use
`--no-embed`, or any other provider, which falls back to the local hash embedder.

The data files are pipe-separated, one city header followed by its places:

```
=City|CC[|alias_or_@lat,lng,tz]
name|lat|lng|cat|tags|tier|cost|blurb[|minutes]
```

Editing a city header means regenerating the metadata:
`python -m scripts.build_world_cities` (needs `pip install geonamescache`).

**How accurate is it?** Worth being straight about, because the UI presents it
as fact:

- City centres, countries and timezones come from **GeoNames** and are reliable.
- Place **names and coordinates** were written from a language model's memory.
  Each was checked against its city centre (anything over 65 km fails the
  validator) and overlapping points were nudged apart — but a pin can still be a
  few hundred metres out. Spot-check before publishing.
- **Ratings, popularity and prices are derived** from an editorial tier and cost
  band, not from reviews or published price lists. They are estimates.
- **Opening hours are category-level approximations.** `wheelchair_accessible` is
  left unknown for every world place, never asserted as true.

## Route optimisation

Six algorithms share one evaluator, so their plan costs are directly comparable
(cost = travel + visit minutes, plus a penalty for each place left out; lower is
better). Measured over 20 random instances of 10-60 places:

| `OPTIMIZER_ALGORITHM` | Gap to best plan | Typical solve |
|---|---|---|
| `ortools` *(default)* | 0.01% | ~2 s |
| `simulated_annealing` | 0.4% | ~0.1 s |
| `genetic` | 1.1% | ~0.1 s |
| `local_search` | 1.5% | ~0.1 s |
| `cheapest_insertion` | 7.0% | ~0.1 s |
| `nearest_neighbor` | 72% | ~0.1 s |
| `auto` | runs all, keeps the best | sum of the above |

Randomised algorithms take a `seed`, so the same request always returns the same
plan. Endpoints:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/itineraries/optimize-route` | Plan a set of places; accepts `algorithm` |
| `POST` | `/api/v1/itineraries/optimize-route/compare` | Run several and compare their plans |
| `GET` | `/api/v1/itineraries/optimizer/algorithms` | List the algorithms and their trade-offs |

Pass `start_date` on either request when planning a specific day — opening hours
are checked against that date's weekday, and it defaults to today.

### Which algorithm runs

Four places can name an algorithm. They are consulted in this order, and the
first one that answers wins:

1. **The request** — `algorithm` on an `optimize-route` call. Applies to that
   call only.
2. **The plan** — `constraints.optimizer_algorithm` on the trip, set from the
   selector in the plan wizard and the day view. Saved with the trip, so it
   survives a reload and every later re-plan.
3. **The environment** — `OPTIMIZER_ALGORITHM`, the deployment-wide default.
4. **`auto`** — a last resort if the environment variable is empty. It runs every
   algorithm and keeps the best plan: safe, just slower.

The order lives in one function, `engine.resolve_algorithm`, which is the only
thing that decides it.

A trip created before the field existed has `optimizer_algorithm: null`, which
falls through to the environment exactly as it did before — so there is nothing
to migrate. An unknown algorithm name is rejected with `422` by the enum at the
API boundary, never a `500`.

The frontend surfaces the comparison in the day view, under the map
("Compare route algorithms"), with the selector beside it.

### Road distances: estimated or measured

By default every distance and travel time is an **estimate**: straight-line
distance times `ROAD_DISTANCE_FACTOR` (1.3), at a typical speed per mode. It
needs no network and never fails.

Set `ROUTING_PROVIDER=openrouteservice` (with `ROUTING_API_KEY`) or
`ROUTING_PROVIDER=osrm` (with `ROUTING_BASE_URL` pointing at your own server) to
measure them on the road network instead. One matrix call covers every point of
a request, and every algorithm, the comparison's scoring, and the legs of an
applied route all read that same matrix, so their figures always agree.

If the provider is slow, down, rate-limiting, or returns something unusable, the
request still succeeds on the estimate. After `ROUTING_BREAKER_THRESHOLD`
consecutive failures the provider is skipped for
`ROUTING_BREAKER_COOLDOWN_SECONDS`. Responses carry `"estimated": true|false`
and `"routing_provider"` so the UI never presents a guess as a measurement;
itineraries record the same on `optimization` and on each travel leg.
Generation weighs many candidates at once, so it is estimated whenever the pool
exceeds `ROUTING_MAX_LOCATIONS`.

## Maps

The maps need **no API key**: Leaflet over openly-licensed tiles — CARTO Voyager
("Clear"), OpenStreetMap ("Streets") and Esri ("Satellite").

Those are free community servers with usage policies. Before real traffic, point
`NEXT_PUBLIC_MAP_TILE_URL` and `NEXT_PUBLIC_MAP_TILE_ATTRIBUTION` at a hosted
provider (MapTiler, Stadia, Mapbox...). Setting `NEXT_PUBLIC_MAPBOX_TOKEN` adds a
Mapbox style to the switcher but is not required.

## CI/CD

| Workflow | When | What |
|---|---|---|
| `backend.yml` | PRs and pushes touching `backend/` | ruff, mypy (report-only), tests on real MongoDB, migrations + endpoint smoke test, `pip-audit` (report-only), Docker build |
| `frontend.yml` | PRs and pushes touching `frontend/` | `tsc`, ESLint (report-only, see below), `next build`, `npm audit` (report-only), Docker build |
| `deploy.yml` | Push to `main` | build + push images to GHCR → **staging** (migrate, deploy hook, readiness check) → **production** (same, after approval) |

`dependabot.yml` opens weekly update PRs for pip, npm, Docker and Actions.

### One-time GitHub setup

1. **Branch protection** on `main`: require the Backend/Frontend CI checks and a review.
   `deploy.yml` does not re-run tests; it relies on this.
2. **Environments** → create `staging` and `production`. On `production`, add
   **Required reviewers** (this is the manual approval gate).
3. **Environment secrets** (set per environment, same names):

   | Secret | Purpose |
   |---|---|
   | `DEPLOY_HOOK_URL` | Your host's deploy-hook URL (Render, Railway, Coolify, ...). Required. |
   | `MONGODB_URI` | Atlas connection string for that environment. Optional: omit to skip the CI migration step (see note). |

4. **Environment variables** (per environment): `MONGODB_DB_NAME`.
   **Repository variables:** `STAGING_API_URL`, `PRODUCTION_API_URL` (no trailing slash).
   Optional: `RUN_STAGING_VERIFY=true` (full endpoint check on staging; it logs in as
   the seeded demo admin and makes real LLM calls), `BUILD_FRONTEND_IMAGE=true`
   plus `PRODUCTION_SITE_URL` / `NEXT_PUBLIC_*` if you self-host the frontend.
5. **On your host**, set the runtime variables from `backend/.env.example`:
   `ENVIRONMENT`, `SECRET_KEY` (unique per environment), `MONGODB_URI`,
   `REDIS_ENABLED=true` + `REDIS_URI` + `CELERY_*`, `CORS_ORIGINS` (your real frontend
   origin only), `LLM_PROVIDER` and its key, `LOG_JSON=true`, `DEBUG=false`, `SENTRY_DSN`.
   Run **one** `beat` instance only; scale `worker` by adding replicas.

**Migrations from GitHub-hosted runners:** Atlas IP allow-lists cannot practically
include the runners' address ranges. If your database is locked down, leave
`MONGODB_URI` unset and run `sh scripts/migrate.sh` as your host's pre-deploy /
release command instead.

**Rollback:** run the *Deploy* workflow manually (`workflow_dispatch`) with
`image_tag` set to a previous `sha-xxxxxxx`. It skips the build and redeploys that
image through the same staging → production path. Migrations are not rolled back
automatically.

### Frontend hosting

Vercel works as-is (set the `NEXT_PUBLIC_*` variables in the project settings).
Alternatively self-host the image from `frontend/Dockerfile`; `NEXT_PUBLIC_*` values are
baked in at build time, so pass them as `--build-arg`.

## Known issues

- Frontend ESLint reports 16 errors (`react-hooks/set-state-in-effect` in 7 places and
  9 unescaped apostrophes). They pre-date CI, so lint is report-only for now; remove
  `continue-on-error` in `frontend.yml` once they are fixed.
- Backend `mypy` reports type errors and is report-only for the same reason.
- Access tokens are kept in `localStorage`. Add a Content Security Policy before launch.
