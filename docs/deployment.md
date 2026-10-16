# Deploying: Atlas + Render + Vercel

The supported production shape:

| Piece | Where | From |
|---|---|---|
| Database | **MongoDB Atlas** | — |
| API | **Render** web service (Docker) | `backend/Dockerfile` |
| Web app | **Vercel** | `frontend/` |

Every backend setting is listed, with its default, in
[`backend/.env.example`](../backend/.env.example) (a test keeps that file and the
code in step). The tables below are the subset you set per environment.

---

## 1. Atlas

1. Create a cluster and a database user with `readWrite` on your database only.
2. **Network Access:** allow Render's outbound IPs (Render dashboard → your service
   → *Connect* → *Outbound*). Avoid `0.0.0.0/0` outside a throwaway project.
3. Copy the `mongodb+srv://` connection string for `MONGODB_URI`.
4. Vector search is optional. With `MONGODB_VECTOR_SEARCH_ENABLED=true` the API
   creates the `vectorSearch` index at startup and keeps it in step with
   `EMBEDDING_DIMENSIONS` (definition in [`backend/README.md`](../backend/README.md#embeddings)).
   Without it, semantic search uses the in-process cosine fallback.

## 2. Render (API)

New **Web Service** → this repository → **Root Directory** `backend`, **Runtime**
Docker. Health check path: `/api/v1/health/ready` (it fails while MongoDB is
unreachable, so Render will not route traffic to a broken instance).

**Pre-deploy command:** `sh scripts/migrate.sh`

### Required

| Variable | Value |
|---|---|
| `ENVIRONMENT` | `production` (or `staging`) |
| `PORT` | `8000` — the port the image listens on |
| `SECRET_KEY` | 32+ random bytes, unique per environment. The API refuses to start on the development default. |
| `MONGODB_URI` | Atlas connection string |
| `MONGODB_DB_NAME` | e.g. `itinerary_planner` |
| `CORS_ORIGINS` | Your Vercel origin(s), comma-separated, e.g. `https://your-app.vercel.app`. Never `*`. |
| `LLM_PROVIDER` | `groq` |
| `GROQ_API_KEY` | From console.groq.com/keys |
| `GROQ_MODEL` | A current model from console.groq.com/docs/models. There is no built-in default; the API refuses to start on Groq without it. |
| `DEBUG` | `false` |
| `LOG_JSON` | `true` |

### Recommended

| Variable | Value | Why |
|---|---|---|
| `GROQ_FAST_MODEL` | a smaller Groq model | Cheaper lightweight calls; falls back to `GROQ_MODEL` |
| `SENTRY_DSN` | your DSN | Error reporting |
| `MONGODB_VECTOR_SEARCH_ENABLED` | `true` on Atlas | Indexed semantic search |
| `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` | `120` / `60` | Per-client request limit |
| `PHOTOS_USER_AGENT` | `YourApp/1.0 (https://your-site)` | Wikimedia asks clients to identify themselves |
| `BUILD_SHA` | set automatically | Render provides `RENDER_GIT_COMMIT`; Docker images get it as a build arg |

### Optional

| Variable | Default | Enables |
|---|---|---|
| `REDIS_ENABLED`, `REDIS_URI`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | off | Shared rate limits across replicas, background generation (`/generate-async`). Needed only with more than one instance. |
| `ROUTING_PROVIDER`, `ROUTING_API_KEY`, `ROUTING_BASE_URL` | `haversine` | Measured road distances (`openrouteservice`, or `osrm` on your own server). Falls back to the estimate on any failure. |
| `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` (+ `GOOGLE_GEMINI_KEY` or `OPENAI_API_KEY`) | `hash` | Learned embeddings. Run `python -m scripts.reembed` after changing. |
| `GOOGLE_CLIENT_ID`, `APPLE_CLIENT_ID` | unset | Federated sign-in (must match the frontend's) |
| `OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT` | off | Tracing |

### One-off jobs (Render Shell), in order

```bash
python -m scripts.seed_db            # places and the demo users; never pass --reset in production
python -m scripts.reembed            # idempotent; required once after this release
python -m scripts.fetch_city_photos  # resumable; ~4 minutes for 103 cities
```

`seed_db` creates demo accounts (see its output). Change or delete them before
the site is public.

## 3. Vercel (web app)

Import the repository → **Root Directory** `frontend` → framework *Next.js*.

`NEXT_PUBLIC_*` values are inlined at **build** time: set them before the first
deploy, and redeploy after changing one. Never put a secret in one.

| Variable | Required | Value |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | yes | Your Render URL, no trailing slash, no `/api/v1` |
| `NEXT_PUBLIC_SITE_URL` | yes | This site's public URL |
| `TILE_USER_AGENT` | recommended | `YourApp/1.0 (https://your-site)` — sent to the map tile servers, whose policies require an identifying agent (server-side only) |
| `NEXT_PUBLIC_MAP_TILE_URL`, `NEXT_PUBLIC_MAP_TILE_ATTRIBUTION` | before real traffic | A hosted tile provider (MapTiler, Stadia, …). The defaults are free community servers with usage limits. |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | optional | Same client id as the API's `GOOGLE_CLIENT_ID` |
| `NEXT_PUBLIC_APPLE_CLIENT_ID`, `NEXT_PUBLIC_APPLE_REDIRECT_URI` | optional | Sign in with Apple |

After the first Vercel deploy, put its URL in the API's `CORS_ORIGINS` and
redeploy the API.

## 4. Verify

```bash
curl -fsS https://YOUR-API/api/v1/health/ready
```

Then open the site, sign in, plan a trip, and open a destination page (its photo
should carry a credit). `GET /api/v1/health/details` (admin) shows the AI
provider, the embedding space and how much of the corpus is in it.

To run the full end-to-end check, point `scripts/verify_endpoints.py` at a
**staging** API with a seeded throwaway database only — it creates and deletes
data.

`/api/v1/health` reports `build`: the commit the instance runs (Render sets
`RENDER_GIT_COMMIT` on its own builds). Check it after a deploy or rollback to
confirm which version is actually serving. The GitHub *Deploy* workflow does
this automatically and fails if the expected build never appears.

## Security headers

The web app sends a `Content-Security-Policy-Report-Only` header (see
`frontend/next.config.ts`): violations appear in the browser console and nothing
is blocked. After a release with no violations, rename the header to
`Content-Security-Policy` to enforce it. Tokens are still kept in
`localStorage`; see [`proposals/cookie-auth.md`](proposals/cookie-auth.md).

## Rollback

Render: *Manual Deploy* → pick the previous deploy, or redeploy its commit. Vercel:
*Deployments* → previous → *Promote to Production*. Then confirm with
`curl https://YOUR-API/api/v1/health` that `build` names the commit you rolled
back to. Migrations are not reversed
automatically; this release's are additive (new optional fields and a new
`city_photos` collection), so the previous version runs against the new data.
