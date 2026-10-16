# AI Itinerary Planner — Backend

FastAPI + MongoDB backend for an AI travel planner. An LLM decides **what is
worth doing**; Google OR-Tools decides **when and in what order**.

Phase 1 of the project. The frontend (Next.js) is Phase 2 and consumes this API.

---

## Why this split

Language models are good at reading "we love history and good food, relaxed
pace, travelling with a toddler" and knowing that a three-hour modern art
gallery is a bad fit. They are bad at arithmetic over opening hours, travel
times and daily limits — ask one to schedule nine places across three days and
it will confidently produce a plan where you are in two places at once.

Constraint solvers are the reverse. So the pipeline hands each half of the
problem to the tool that is actually good at it:

```
User brief
    ↓
LLM  ──────────────  parse intent, score candidate places for this traveller
    ↓
Python planning engine  ── recall, filter, rank, blend with objective signals
    ↓
OR-Tools  ─────────  schedule as a VRP with time windows (1 vehicle = 1 day)
    ↓
Constraint validation
    ↓
Final itinerary  ──  narrated back by the LLM
```

---

## Stack

Adapted from the reference architecture, with **MongoDB replacing PostgreSQL**.

| Concern | Reference stack | Used here | Why |
|---|---|---|---|
| Database | PostgreSQL | **MongoDB** | As specified for this project |
| ORM/ODM | SQLAlchemy | **Beanie** (on Motor) | Pydantic-native async ODM — the models *are* the schema |
| Migrations | Alembic | **Beanie migrations** + index sync | Beanie creates declared indexes at startup |
| Geospatial | PostGIS | **2dsphere indexes** | `$nearSphere` / `$geoWithin`, GeoJSON points |
| Vectors | pgvector | **Atlas Vector Search** + cosine fallback | `$vectorSearch` on Atlas; in-process scan elsewhere |
| API | FastAPI, Pydantic v2 | same | |
| Optimisation | OR-Tools, NetworkX, NumPy | same | |
| AI | Anthropic / OpenAI / Gemini, LangGraph | Gemini and Groq (primary), Anthropic/OpenAI, + a deterministic stub | Stub lets the full pipeline run with no API key |
| Queue | Celery + Redis | same | |
| Observability | Sentry, Prometheus, OpenTelemetry, structlog | same | |

**Deviation worth flagging:** LlamaIndex is not used. Retrieval here is a
two-path union — a `2dsphere` radius query plus a vector-similarity query,
both straight against MongoDB. Adding a RAG framework on top of two native
database queries would be indirection without benefit. If retrieval grows to
span external corpora, that is the point to reconsider.

---

## Quick start

### Docker (everything at once)

```bash
docker compose up --build
```

Then seed and open the docs:

```bash
docker compose exec api python -m scripts.seed_db --reset
```

API docs: <http://localhost:8000/docs>

### Local (Python 3.12 + a MongoDB you already run)

```bash
python -m venv .venv && ./.venv/Scripts/activate      # Windows
python -m venv .venv && source .venv/bin/activate      # macOS / Linux
pip install -r requirements-dev.txt
cp .env.example .env
python -m scripts.seed_db --reset
uvicorn app.main:app --reload --port 8000
```

Seeding creates two accounts:

| Email | Password | Role |
|---|---|---|
| `demo@example.com` | `demopassword1` | traveller |
| `admin@example.com` | `adminpassword1` | admin |

…and two sets of places:

| Set | Size | Source |
|---|---|---|
| Core | 37 places across Paris, Rome and Tokyo | hand-written, `app/db/seed.py` |
| World | 2,000 places across 100 cities | `app/db/world_data/*.txt` via `app/db/world_seed.py` |

```bash
python -m scripts.seed_db --reset       # drop, then seed both sets
python -m scripts.seed_db --no-world    # core set only
python -m scripts.seed_db --no-embed    # skip embeddings
python -m scripts.validate_world_data   # structural + geographic checks
```

With `LLM_PROVIDER=openai`, embedding 2,000 places is 2,000 API calls — use
`--no-embed`, or any other provider, which uses the local hash embedder.

**On the world data's accuracy.** City centres, countries and timezones come from
GeoNames and are reliable. Place names and coordinates were written from a
language model's memory: each is validated against its city centre (over 65 km
fails) and overlapping points were separated, but a pin may still be a few hundred
metres out. Ratings, popularity and prices are *derived* from an editorial tier
and cost band — estimates, not reviews or price lists. Opening hours are
category-level approximations, and `wheelchair_accessible` is left unknown for
every world place rather than asserted. Keep any UI copy consistent with that.

### Optional: real LLM providers

Gemini and Groq are the primary providers. Pick one and set its key:

```bash
pip install -r requirements-ai.txt
# then in .env, EITHER:
LLM_PROVIDER=gemini
GOOGLE_GEMINI_KEY=...        # https://aistudio.google.com/apikey

# OR:
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...         # https://console.groq.com/keys
GROQ_MODEL=...               # https://console.groq.com/docs/models
```

Gemini defaults to `gemini-3.8-flash` when `LLM_MODEL` is unset. Groq has **no**
default: name the model in `GROQ_MODEL` (and optionally `GROQ_FAST_MODEL`), since
Groq retires models too often for a built-in ID to stay valid. Deployed, the API
refuses to start on Groq without `GROQ_API_KEY` and `GROQ_MODEL`. Groq calls
have a timeout (`LLM_TIMEOUT_SECONDS`) and retry with backoff on timeouts, 429
and 5xx (`LLM_MAX_RETRIES`); a rate limit that outlasts the retries is returned
as a 429 that says how long to wait. `GEMINI_API_KEY` is still accepted as an
alias for `GOOGLE_GEMINI_KEY`. Anthropic and OpenAI remain available
(`LLM_PROVIDER=anthropic|openai`; OpenAI also needs `LLM_MODEL`).

If the chosen provider's key is missing, the service logs a warning and runs on
the stub instead of failing to start.

Without a key the service runs on `LLM_PROVIDER=stub` — a deterministic,
offline provider. The rest of the pipeline, including the solver, is identical,
which is what makes CI meaningful.

### Embeddings

Semantic search embeds places with `EMBEDDING_PROVIDER`, which is separate from
the LLM. The default, `hash`, needs no key or network, so the app works end to
end with nothing but `GROQ_API_KEY` (Groq has no embeddings API). For learned
embeddings set `EMBEDDING_PROVIDER=gemini|openai|local` plus `EMBEDDING_MODEL`
and, for the hosted ones, their key.

Every stored vector records the provider, model and dimension that made it
(`embedding_space`, e.g. `hash:hash-v1:384`), and search only compares vectors
from the configured space — there is no silent fallback to another embedder, so
a provider outage is an error instead of a quietly corrupted index. After
changing the provider, model or `EMBEDDING_DIMENSIONS`:

```bash
python -m scripts.reembed --dry-run   # how many places need it
python -m scripts.reembed             # do it (idempotent; re-run to resume)
```

Until it runs, places embedded the old way are invisible to semantic search
(geographic recall still works). `GET /api/v1/health/details` reports how many
places are in the current space. A run stopped by a rate limit exits with code
2 and picks up where it left off next time.

On Atlas, the index the app creates (and updates in place if the dimension or
fields change) is:

```json
{
  "name": "place_embedding_index",
  "type": "vectorSearch",
  "definition": {
    "fields": [
      {"type": "vector", "path": "embedding", "numDimensions": 384, "similarity": "cosine"},
      {"type": "filter", "path": "city"},
      {"type": "filter", "path": "category"},
      {"type": "filter", "path": "is_active"},
      {"type": "filter", "path": "embedding_space"}
    ]
  }
}
```

`numDimensions` must equal `EMBEDDING_DIMENSIONS`; the name is
`MONGODB_VECTOR_INDEX_NAME`.

### City photos

Destination cards and city pages show a freely licensed photo of the city from
Wikimedia Commons, credited with its author and licence (linked on the city
page). Fetch them once per environment:

```bash
python -m scripts.fetch_city_photos                  # cities not yet checked
python -m scripts.fetch_city_photos --retry-missing  # also retry cities with none
python -m scripts.fetch_city_photos --refresh        # re-check everything
```

A photo is kept only if the file is on Commons under CC0, public domain, CC BY
or CC BY-SA (never NC/ND or fair use), the Wikipedia article's coordinates are
within 50 km of the city's own centre (so a same-named town elsewhere is never
used), and it is a photograph rather than a map, flag or emblem. Every city
checked is recorded either way, so reruns resume instead of starting over; the
fetch is sequential, paced, and backs off on 429/503. A city with no usable
photo falls back to the curated photo, then to a plain gradient — never an
unrelated stock image. On the 103 seeded cities the first run found 87.

---

## API

49 operations under `/api/v1`. Full schema at `/docs`.

### Auth
| Method | Path | Notes |
|---|---|---|
| POST | `/auth/register` | Argon2id hashing |
| POST | `/auth/login` | |
| POST | `/auth/refresh` | **Single-use rotation** — replay is rejected |
| POST | `/auth/logout` · `/auth/logout-all` | |
| POST | `/auth/change-password` | Revokes every session |

### Places
| Method | Path | Notes |
|---|---|---|
| GET | `/places` | Filter by city, category, tags, rating |
| GET | `/places/nearby` | `2dsphere` radius search, distance-ordered |
| GET | `/places/search` | Full-text, weighted name > city > description |
| POST | `/places/semantic-search` | Vector search |
| GET | `/places/cities` | Aggregation for destination pickers |
| POST/PATCH/DELETE | `/places...` | Admin; delete is soft by default |

### Trips & itineraries
| Method | Path | Notes |
|---|---|---|
| POST | `/trips` | Unset constraints inherit from the user profile |
| POST | `/trips/{id}/generate` | **The pipeline.** 422 when no feasible plan exists |
| POST | `/trips/{id}/generate-async` | Same, queued on a worker; 503 without a broker |
| GET | `/trips/tasks/{task_id}` | Poll a queued run |
| GET | `/trips/{id}/itineraries` | Every version |
| POST | `/trips/{id}/share` | Public link behind an unguessable token |
| POST | `/itineraries/{id}/reorder` | Times and travel legs recomputed server-side |
| POST | `/itineraries/optimize-route` | Solver over arbitrary places, no trip needed; takes `algorithm` |
| POST | `/itineraries/optimize-route/compare` | Run several algorithms on the same places and compare |
| GET | `/itineraries/optimizer/algorithms` | The algorithms available, with their trade-offs |
| GET | `/ai/runs` | Per-stage audit trail for every generation |

Both route endpoints take `start_date`. Opening hours are checked against that
date's weekday, so pass it when planning a specific day; it defaults to today.

---

## How the optimiser is modelled

One **vehicle per day**, all sharing a single global timeline where day *d*
owns minutes `[d·1440, (d+1)·1440)`. A single solve therefore decides both
*which day* each place lands on and *what order* it is visited in. Clustering
places by day first and routing each day separately — the obvious approach —
cannot trade a stop between days, and produces visibly worse plans.

Constraints given to the solver:

- **Opening hours** — per-node time windows. The union of visitable windows
  across days is expressed as one range with the closed gaps punched out via
  `RemoveInterval`.
- **Overnight gaps** — removed from every node's domain, so no activity
  straddles midnight.
- **Visit duration** — service time, folded into the transit callback.
- **Pace** — a counting dimension caps places per day (relaxed 3 / moderate 5 /
  packed 7).
- **Daily distance** — a distance dimension caps how far a day may range.
- **Optional visits** — score-weighted drop penalties, so the solver sheds the
  least interesting places when time runs out instead of failing. Places the
  traveller marked mandatory are priced ~10⁴× higher.
- **Unvisitable places** — a place closed on every day of the trip is pinned
  unperformed (`VehicleVar = -1`), not merely made cheap to drop.

If OR-Tools is unavailable or returns nothing, a greedy nearest-neighbour
construction with a 2-opt pass takes over. It honours the same hard constraints
and is covered by the same tests; it simply makes no optimality claim.

Typical effect on the seeded corpus: **27 km → 19 km** over three days in Paris,
**18 km → 13 km** for a six-stop Rome route.

### Choosing an algorithm

`app/optimization/algorithms.py` adds five alternatives to OR-Tools. The point
that makes them comparable: **one evaluator scores every plan**, including
OR-Tools' own after conversion, reproducing the OR-Tools objective exactly. Plan
costs are therefore directly comparable, and lower is better.

Set `OPTIMIZER_ALGORITHM`, or pass `algorithm` per request. Measured over 20
random instances of 10-60 places:

| Value | Gap to best plan | Typical solve |
|---|---|---|
| `ortools` *(default)* | 0.01% | ~2 s |
| `simulated_annealing` | 0.4% | ~0.1 s |
| `genetic` | 1.1% | ~0.1 s |
| `local_search` | 1.5% | ~0.1 s |
| `cheapest_insertion` | 7.0% | ~0.1 s |
| `nearest_neighbor` | 72% | ~0.1 s |
| `auto` | runs all, keeps the best | sum of the above |

Two details worth knowing:

- `OPTIMIZER_TIME_LIMIT_SECONDS` applies **only** to OR-Tools. The others stop on
  iteration counts, so their output does not vary with machine speed.
- The randomised algorithms take a `seed` (default 42), so a given request always
  produces the same plan.

Solves run in a worker thread via `asyncio.to_thread`, so they never block the
event loop. Keep any new solver endpoint on that path.

---

## Testing

```bash
pytest                               # everything
pytest tests/test_optimization.py    # solver only, no database needed
pytest -m geo                        # 2dsphere / radius search
pytest -m vector                     # semantic search
pytest -m "not vector"               # everything else
pytest --cov=app --cov-report=term-missing
```

Tests run against a **real MongoDB** on a throwaway database, not a mock —
geospatial queries, text indexes and TTL behaviour are precisely the parts a
mock would fail to exercise. Point at another server with `TEST_MONGODB_URI`.
If no server is reachable the database-backed tests skip rather than fail, so
the pure-unit tests still run anywhere — except under `REQUIRE_MONGODB=1`, which
CI sets so a missing service container fails the run instead of passing it with
the database tests silently skipped.

There is also a live end-to-end check against a running server:

```bash
python -m scripts.verify_endpoints --base-url http://127.0.0.1:8000
```

It walks the real journey — register, plan, edit, compare routes, apply one,
choose an algorithm, share, clean up — and asserts on the invariants that
matter: no overlapping activities, the daily window respected, no place
scheduled twice, the optimised route no longer than the naive one, a stale
route comparison refused with 409, and one user unable to read another's trip.
It creates and deletes data, so point it at a seeded throwaway database (CI's
smoke job does exactly that), never at one you care about.

---

## Project layout

```
app/
├── api/v1/endpoints/   # HTTP layer only — no business logic
├── ai/                 # LLM providers, prompts, embeddings, pipeline, graph
├── core/               # config, security, deps, exceptions, time helpers
├── db/                 # connection, index management, seed data
├── middleware/         # request context, rate limiting
├── models/             # Beanie documents (the schema)
├── observability/      # structlog, Prometheus, OTel/Sentry
├── optimization/       # OR-Tools engine, distance maths
├── schemas/            # request/response DTOs
├── services/           # business logic
└── workers/            # Celery app and tasks
```

The layering rule: **endpoints → services → models**. Endpoints parse and
serialise; services hold the logic; models own persistence. The optimiser knows
nothing about HTTP or MongoDB, which is why it is testable on its own.

---

## Operational notes

- **Startup fails loudly** in staging/production if `SECRET_KEY` is still the
  development default or is shorter than 32 bytes.
- **Rate limiting** uses Redis when `REDIS_ENABLED=true`, so the limit holds
  across replicas. The in-process fallback is per-process: with N replicas the
  effective limit is N× the configured one. Fine locally, wrong in production.
- **Vector search** needs `MONGODB_VECTOR_SEARCH_ENABLED=true` and an Atlas
  deployment. Elsewhere it falls back to an in-process cosine scan, which is
  O(n) and scoped to a city — correct, but not a production retrieval path at
  scale.
- **Soft deletes** are the default for places, because saved itineraries
  reference them by id.
- `/metrics` exposes Prometheus counters including `itineraries_generated_total`,
  `optimizer_runs_total`, `optimizer_duration_seconds` and `llm_tokens_total`.

## Known limitations

- Travel time is estimated (haversine distance × 1.3 at per-mode speeds) unless
  `ROUTING_PROVIDER` is set, and stays estimated for transit and for generation
  pools larger than `ROUTING_MAX_LOCATIONS`. Responses say which via `estimated`.
- The hashing embedder used without an API key captures lexical overlap, not
  learned semantics. Retrieval behaves sensibly, but it is not a substitute for
  real embeddings in production.
- `POST /trips/{id}/generate-async` needs Redis and a running Celery worker.
  Without a broker it returns 503 rather than pretending to queue; the
  synchronous `/generate` is always available.
