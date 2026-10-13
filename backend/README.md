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

Seeding creates 37 real places across Paris, Rome and Tokyo, plus two accounts:

| Email | Password | Role |
|---|---|---|
| `demo@example.com` | `demopassword1` | traveller |
| `admin@example.com` | `adminpassword1` | admin |

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
```

Leave `LLM_MODEL` unset to use the provider default (`gemini-3.8-flash`, or
`openai/gpt-oss-120b` on Groq). Providers retire models on a schedule, so check
their model pages before pinning one. `GEMINI_API_KEY` is still accepted as an
alias for `GOOGLE_GEMINI_KEY`. Anthropic and OpenAI remain available
(`LLM_PROVIDER=anthropic|openai`; OpenAI also needs `LLM_MODEL`).

If the chosen provider's key is missing, the service logs a warning and runs on
the stub instead of failing to start.

Without a key the service runs on `LLM_PROVIDER=stub` — a deterministic,
offline provider. The rest of the pipeline, including the solver, is identical,
which is what makes CI meaningful.

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
| POST | `/itineraries/optimize-route` | Solver over arbitrary places, no trip needed |
| GET | `/ai/runs` | Per-stage audit trail for every generation |

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

---

## Testing

```bash
pytest                               # everything
pytest tests/test_optimization.py    # solver only, no database needed
pytest --cov=app --cov-report=term-missing
```

Tests run against a **real MongoDB** on a throwaway database, not a mock —
geospatial queries, text indexes and TTL behaviour are precisely the parts a
mock would fail to exercise. Point at another server with `TEST_MONGODB_URI`.
If no server is reachable the database-backed tests skip rather than fail, so
the pure-unit tests still run anywhere.

There is also a live end-to-end check against a running server:

```bash
python -m scripts.verify_endpoints --base-url http://127.0.0.1:8000
```

It walks the real journey — register, plan, edit, share, clean up — and asserts
on the invariants that matter: no overlapping activities, the daily window
respected, no place scheduled twice, the optimised route no longer than the
naive one, and one user unable to read another's trip.

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

- Travel time is haversine distance × 1.3 with per-mode speeds, not real routing.
  `build_travel_matrix` is the single seam to swap in the Mapbox Matrix API.
- The hashing embedder used without an API key captures lexical overlap, not
  learned semantics. Retrieval behaves sensibly, but it is not a substitute for
  real embeddings in production.
- `POST /trips/{id}/generate-async` needs Redis and a running Celery worker.
  Without a broker it returns 503 rather than pretending to queue; the
  synchronous `/generate` is always available.
