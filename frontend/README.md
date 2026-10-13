# Frontend

Next.js 16 (App Router) + React 19 client for the AI Itinerary Planner.
The project overview lives in the [root README](../README.md).

## Run it

```bash
cp .env.example .env.local     # NEXT_PUBLIC_API_URL must point at the backend
npm ci
npm run dev                    # http://localhost:3000
```

`NEXT_PUBLIC_*` values are inlined into the browser bundle at **build** time and
are never secrets. See [`.env.example`](.env.example) for the full list.

## Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Dev server with hot reload |
| `npm run build` | Production build (emits `.next/standalone` for Docker) |
| `npm run lint` | ESLint. CI treats any error as a failure |
| `npx tsc --noEmit` | Type-check |

## Structure

```text
src/
├── app/            Routes: home, plan, trips, destinations, account, shared/[token], sign-in/up
├── components/     auth, trip (wizard, timeline), places, map (Leaflet), layout, ui (Radix wrappers)
├── hooks/          use-script and other small hooks
├── lib/            api client, map helpers, image helpers, utils
├── store/          Zustand auth store
└── types/          API types
```

## Conventions

- Server state goes through TanStack Query; client state through Zustand.
- Prefer deriving values during render over syncing state in an effect. The
  `react-hooks/set-state-in-effect` rule enforces this; if a one-time sync from
  async data is genuinely needed, disable the rule on that line with a reason.
- Remote images are allow-listed in `next.config.ts`; add a host there before using it.

## Docker

```bash
docker build -t itinerary-frontend \
  --build-arg NEXT_PUBLIC_API_URL=https://api.example.com \
  --build-arg NEXT_PUBLIC_SITE_URL=https://app.example.com .
docker run -p 3000:3000 itinerary-frontend
```

The build fetches Google Fonts (`next/font`), so it needs outbound network access.
