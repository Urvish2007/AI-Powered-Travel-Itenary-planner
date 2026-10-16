# Proposal: refresh token in an httpOnly cookie

**Status:** proposed, not implemented — needs a decision (see *Decision needed*).

## Today

Both tokens live in `localStorage` (`frontend/src/lib/api.ts`). Any script that
runs on the site can read them, so one XSS bug — in our code or a dependency —
hands an attacker a **30-day refresh token** that keeps minting access tokens
after the tab is closed. Mitigations already in place: short access tokens
(60 min), single-use rotating refresh tokens, and now a CSP (report-only).

## Proposal

| Token | Where | Lifetime | Readable by scripts |
|---|---|---|---|
| Access | memory only (a module variable) | 60 min | yes, but gone on reload |
| Refresh | `httpOnly; Secure; SameSite=Lax` cookie, `Path=/api/v1/auth` | 30 days | **no** |

- `POST /auth/login`, `/register`, `/refresh`, OAuth: set the refresh cookie and
  return only the access token in the body.
- `POST /auth/refresh` reads the cookie, rotates it, returns a new access token.
  On page load the app calls it once to restore the session.
- `POST /auth/logout` revokes the token and clears the cookie.
- CSRF: the cookie is only sent to the three auth paths, which take no
  user-chosen input that changes data besides issuing tokens; `SameSite=Lax`
  plus a required `X-Requested-With` header on `/refresh` closes cross-site use.

An XSS bug can still act as the user while the page is open — no storage choice
fixes that — but it can no longer walk away with a long-lived credential.

## The catch: Vercel and Render are different sites

`your-app.vercel.app` and `your-api.onrender.com` are different registrable
domains, so the cookie would be **third-party**. That requires `SameSite=None`,
and browsers increasingly block third-party cookies outright (Safari already
does), which would sign users out at random. Two ways to make it first-party:

1. **Custom domains** (recommended): `app.example.com` (Vercel) and
   `api.example.com` (Render) share `example.com`, so the cookie is same-site.
   Needs a domain you control; no code beyond the above.
2. **Proxy through Next.js:** the browser calls `/api/backend/*` on the Vercel
   origin and a route handler forwards to Render. Works on `*.vercel.app`, but
   every API call takes an extra hop and the proxy must forward headers and
   errors faithfully.

## Compatibility

Additive and staged: the API keeps accepting `Authorization: Bearer` and keeps
returning `refresh_token` in the body behind a flag (`AUTH_REFRESH_IN_BODY`,
default on) until the frontend has shipped the cookie flow; then the flag is
turned off. Existing sessions continue until their refresh token expires.

## Decision needed

1. Adopt the cookie flow? **Recommended: yes**, before public launch.
2. Custom domains or the Next.js proxy? **Recommended: custom domains.**

Estimated size: backend auth endpoints + tests, the frontend token store and
the session bootstrap; about one focused day, plus the domain setup.
