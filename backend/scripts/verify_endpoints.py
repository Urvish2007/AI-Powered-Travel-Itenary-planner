"""End-to-end verification of every API endpoint against a running server.

Walks the real user journey - register, plan, edit, share, clean up - and
asserts on status codes and response shapes, so a pass means the endpoints
actually work together rather than merely existing.

Usage:
    python -m scripts.verify_endpoints [--base-url http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import date, timedelta
from typing import Any

import httpx

PASS = "PASS"
FAIL = "FAIL"


class Checker:
    """Records one line per endpoint exercised."""

    def __init__(self) -> None:
        self.results: list[tuple[str, str, str, str]] = []
        self.failures = 0

    def record(self, method: str, path: str, ok: bool, detail: str = "") -> None:
        status = PASS if ok else FAIL
        if not ok:
            self.failures += 1
        self.results.append((status, method, path, detail))
        marker = "+" if ok else "x"
        print(f"  [{marker}] {method:6} {path:58} {detail}")

    def check(
        self,
        method: str,
        path: str,
        response: httpx.Response,
        expected: int | tuple[int, ...],
        extra: str = "",
    ) -> bool:
        wanted = (expected,) if isinstance(expected, int) else expected
        ok = response.status_code in wanted
        detail = f"{response.status_code}"
        if not ok:
            detail += f" (wanted {wanted}) {response.text[:160]}"
        elif extra:
            detail += f" {extra}"
        self.record(method, path, ok, detail)
        return ok

    def assertion(self, name: str, ok: bool, detail: str = "") -> bool:
        self.record("ASSERT", name, ok, detail)
        return ok

    def summary(self) -> int:
        total = len(self.results)
        passed = total - self.failures
        print("\n" + "=" * 92)
        print(f"  {passed}/{total} checks passed")
        if self.failures:
            print(f"  {self.failures} FAILURE(S):")
            for status, method, path, detail in self.results:
                if status == FAIL:
                    print(f"    x {method} {path} -> {detail}")
        print("=" * 92)
        return 1 if self.failures else 0


def section(title: str) -> None:
    print(f"\n--- {title} ---")


async def main(base_url: str) -> int:
    check = Checker()
    api = f"{base_url.rstrip('/')}/api/v1"
    suffix = uuid.uuid4().hex[:8]
    email = f"verify-{suffix}@example.com"
    password = "verifyPass123"

    async with httpx.AsyncClient(timeout=120.0) as client:
        # ------------------------------------------------------------------
        section("Health")
        r = await client.get(f"{api}/health")
        check.check("GET", "/health", r, 200)

        r = await client.get(f"{api}/health/ready")
        check.check("GET", "/health/ready", r, 200, f"mongodb={r.json().get('mongodb')}")
        if r.json().get("mongodb") != "up":
            print("\nMongoDB is not reachable; aborting.")
            return check.summary()

        # ------------------------------------------------------------------
        section("Auth")
        r = await client.post(
            f"{api}/auth/register",
            json={
                "email": email,
                "password": password,
                "full_name": "Verification User",
                "home_city": "London",
            },
        )
        check.check("POST", "/auth/register", r, 201)
        if r.status_code != 201:
            return check.summary()
        tokens = r.json()["tokens"]
        access, refresh = tokens["access_token"], tokens["refresh_token"]
        auth = {"Authorization": f"Bearer {access}"}

        # Duplicate registration must conflict.
        r = await client.post(
            f"{api}/auth/register",
            json={"email": email, "password": password, "full_name": "Dup"},
        )
        check.check("POST", "/auth/register (duplicate)", r, 409)

        # Weak password must be rejected.
        r = await client.post(
            f"{api}/auth/register",
            json={"email": f"weak-{suffix}@x.com", "password": "short", "full_name": "W"},
        )
        check.check("POST", "/auth/register (weak password)", r, 422)

        r = await client.post(
            f"{api}/auth/login", json={"email": email, "password": password}
        )
        check.check("POST", "/auth/login", r, 200)

        r = await client.post(
            f"{api}/auth/login", json={"email": email, "password": "wrong-password"}
        )
        check.check("POST", "/auth/login (bad password)", r, 401)

        r = await client.post(f"{api}/auth/refresh", json={"refresh_token": refresh})
        check.check("POST", "/auth/refresh", r, 200)
        if r.status_code == 200:
            rotated = r.json()
            access, new_refresh = rotated["access_token"], rotated["refresh_token"]
            auth = {"Authorization": f"Bearer {access}"}

            # Rotation is single-use: the old token must now fail.
            r = await client.post(f"{api}/auth/refresh", json={"refresh_token": refresh})
            check.check("POST", "/auth/refresh (replay old token)", r, 401)
            refresh = new_refresh

        # Unauthenticated and malformed-token access must both be rejected.
        r = await client.get(f"{api}/users/me")
        check.check("GET", "/users/me (no token)", r, 401)
        r = await client.get(
            f"{api}/users/me", headers={"Authorization": "Bearer not-a-real-token"}
        )
        check.check("GET", "/users/me (bad token)", r, 401)

        # ------------------------------------------------------------------
        section("Users")
        r = await client.get(f"{api}/users/me", headers=auth)
        check.check("GET", "/users/me", r, 200)
        check.assertion(
            "password hash is never returned",
            "hashed_password" not in r.text,
            "absent" if "hashed_password" not in r.text else "LEAKED",
        )

        r = await client.patch(
            f"{api}/users/me", headers=auth, json={"full_name": "Renamed User"}
        )
        check.check("PATCH", "/users/me", r, 200, f"name={r.json().get('full_name')}")

        r = await client.put(
            f"{api}/users/me/preferences",
            headers=auth,
            json={
                "interests": ["history", "art", "food"],
                "pace": "moderate",
                "budget_level": "moderate",
                "preferred_transport": "walking",
                "day_start_time": "09:00",
                "day_end_time": "20:00",
                "max_daily_walking_km": 10.0,
            },
        )
        check.check("PUT", "/users/me/preferences", r, 200)

        r = await client.get(f"{api}/users/me/stats", headers=auth)
        check.check("GET", "/users/me/stats", r, 200, str(r.json()))

        r = await client.get(f"{api}/users", headers=auth)
        check.check("GET", "/users (non-admin forbidden)", r, 403)

        # ------------------------------------------------------------------
        section("Places")
        r = await client.get(f"{api}/places", params={"city": "Paris", "limit": 5})
        check.check("GET", "/places", r, 200, f"total={r.json()['meta']['total']}")

        r = await client.get(f"{api}/places", params={"limit": 999})
        check.check("GET", "/places (limit over max)", r, 422)

        r = await client.get(f"{api}/places/cities")
        cities = r.json() if r.status_code == 200 else []
        check.check("GET", "/places/cities", r, 200, f"{len(cities)} cities")

        r = await client.get(
            f"{api}/places/nearby",
            params={
                "latitude": 48.8584,
                "longitude": 2.2945,
                "radius_km": 5,
                "limit": 10,
            },
        )
        nearby = r.json() if r.status_code == 200 else []
        check.check("GET", "/places/nearby", r, 200, f"{len(nearby)} within 5km")
        if nearby:
            distances = [p["distance_km"] for p in nearby]
            check.assertion(
                "nearby results are distance-ordered",
                distances == sorted(distances),
                f"first={distances[0]} last={distances[-1]}",
            )

        r = await client.get(f"{api}/places/search", params={"q": "museum", "limit": 5})
        check.check("GET", "/places/search", r, 200, f"{len(r.json())} hits")

        r = await client.post(
            f"{api}/places/semantic-search",
            json={"query": "quiet gardens to relax in", "city": "Paris", "limit": 5},
        )
        hits = r.json() if r.status_code == 200 else []
        check.check("POST", "/places/semantic-search", r, 200, f"{len(hits)} hits")

        place_id = nearby[0]["id"] if nearby else None
        if place_id:
            r = await client.get(f"{api}/places/{place_id}")
            check.check("GET", "/places/{id}", r, 200, r.json().get("name", ""))

        r = await client.get(f"{api}/places/000000000000000000000000")
        check.check("GET", "/places/{id} (missing)", r, 404)

        r = await client.get(f"{api}/places/not-an-object-id")
        check.check("GET", "/places/{id} (malformed)", r, 404)

        r = await client.post(
            f"{api}/places",
            headers=auth,
            json={
                "name": "X",
                "city": "Y",
                "country": "Z",
                "latitude": 0,
                "longitude": 0,
            },
        )
        check.check("POST", "/places (non-admin forbidden)", r, 403)

        # ------------------------------------------------------------------
        section("Trips")
        start = date.today() + timedelta(days=30)
        end = start + timedelta(days=2)
        r = await client.post(
            f"{api}/trips",
            headers=auth,
            json={
                "title": "Long weekend in Paris",
                "destination": {
                    "city": "Paris",
                    "country": "France",
                    "latitude": 48.8566,
                    "longitude": 2.3522,
                    "timezone": "Europe/Paris",
                },
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "party": {"adults": 2, "children": 0, "infants": 0},
                "constraints": {
                    "pace": "moderate",
                    "budget_level": "moderate",
                    "currency": "EUR",
                    "interests": ["history", "art", "food"],
                    "preferred_transport": "walking",
                    "day_start_time": "09:00",
                    "day_end_time": "20:00",
                    "max_daily_walking_km": 10.0,
                },
            },
        )
        check.check("POST", "/trips", r, 201)
        if r.status_code != 201:
            return check.summary()
        trip = r.json()
        trip_id = trip["id"]
        check.assertion(
            "trip duration is inclusive (3 days)",
            trip["duration_days"] == 3,
            f"duration_days={trip['duration_days']}",
        )

        # Invalid date ordering must be rejected.
        r = await client.post(
            f"{api}/trips",
            headers=auth,
            json={
                "destination": {
                    "city": "Paris",
                    "country": "France",
                    "latitude": 48.85,
                    "longitude": 2.35,
                },
                "start_date": end.isoformat(),
                "end_date": start.isoformat(),
            },
        )
        check.check("POST", "/trips (end before start)", r, 422)

        r = await client.get(f"{api}/trips", headers=auth)
        check.check("GET", "/trips", r, 200, f"total={r.json()['meta']['total']}")

        r = await client.get(f"{api}/trips/{trip_id}", headers=auth)
        check.check("GET", "/trips/{id}", r, 200)

        r = await client.patch(
            f"{api}/trips/{trip_id}", headers=auth, json={"title": "Paris, revised"}
        )
        check.check("PATCH", "/trips/{id}", r, 200, r.json().get("title", ""))

        # ------------------------------------------------------------------
        section("AI")
        r = await client.post(
            f"{api}/ai/parse-intent",
            headers=auth,
            json={
                "trip_id": trip_id,
                "brief": "We love history and good food, and want a relaxed pace "
                "with plenty of time in museums.",
            },
        )
        check.check("POST", "/ai/parse-intent", r, 200, str(r.json())[:110])

        r = await client.post(
            f"{api}/ai/recommend",
            headers=auth,
            json={"query": "classic art museums", "city": "Paris", "limit": 5},
        )
        check.check("POST", "/ai/recommend", r, 200, f"{len(r.json())} results")

        # ------------------------------------------------------------------
        section("Itinerary generation (LLM -> OR-Tools pipeline)")
        r = await client.post(
            f"{api}/trips/{trip_id}/generate",
            headers=auth,
            json={
                "brief": "History, art and great food. Relaxed pace, lots of walking.",
                "regenerate": False,
            },
        )
        check.check("POST", "/trips/{id}/generate", r, 201)
        if r.status_code != 201:
            print("\nGeneration failed; skipping itinerary checks.")
            return check.summary()

        itinerary = r.json()
        itinerary_id = itinerary["id"]
        days = itinerary["days"]
        opt = itinerary["optimization"]

        print(f"\n      title      : {itinerary['title']}")
        print(f"      days       : {len(days)}")
        print(f"      activities : {sum(len(d['activities']) for d in days)}")
        print(f"      distance   : {itinerary['total_distance_km']} km")
        print(
            f"      cost       : {itinerary['total_estimated_cost']} "
            f"{itinerary['currency']}"
        )
        print(f"      solver     : {opt['solver_status']} in {opt['duration_seconds']}s")
        print(
            f"      scheduled  : {opt['places_scheduled']}, dropped {opt['places_dropped']}"
        )
        print(
            f"      before/after: {opt['distance_before_km']} -> "
            f"{opt['distance_after_km']} km"
        )

        check.assertion(
            "itinerary has one day per trip day",
            len(days) == 3,
            f"{len(days)} days",
        )
        check.assertion(
            "the optimiser actually ran",
            opt["ran"] is True and opt["solver_status"] not in ("not_run", ""),
            f"status={opt['solver_status']}",
        )
        check.assertion(
            "at least one activity was scheduled",
            sum(len(d["activities"]) for d in days) > 0,
        )
        _verify_schedule(check, days)

        # Non-regenerating call returns the existing version.
        r = await client.post(
            f"{api}/trips/{trip_id}/generate", headers=auth, json={"regenerate": False}
        )
        same = r.status_code == 201 and r.json()["version"] == itinerary["version"]
        check.check(
            "POST",
            "/trips/{id}/generate (no regenerate)",
            r,
            201,
            f"version={r.json().get('version')} reused={same}",
        )

        r = await client.post(
            f"{api}/trips/{trip_id}/generate",
            headers=auth,
            json={"brief": "Focus on food.", "regenerate": True},
        )
        check.check(
            "POST",
            "/trips/{id}/generate (regenerate)",
            r,
            201,
            f"version={r.json().get('version')}",
        )
        check.assertion(
            "regeneration creates a new version",
            r.status_code == 201 and r.json()["version"] == itinerary["version"] + 1,
        )

        r = await client.get(f"{api}/trips/{trip_id}/itineraries", headers=auth)
        check.check("GET", "/trips/{id}/itineraries", r, 200, f"{len(r.json())} versions")

        r = await client.get(f"{api}/trips/{trip_id}/itinerary", headers=auth)
        check.check("GET", "/trips/{id}/itinerary", r, 200)

        # ------------------------------------------------------------------
        section("Background processing")
        # Without a Celery broker this must refuse cleanly rather than hang or
        # pretend the work was queued.
        r = await client.post(
            f"{api}/trips/{trip_id}/generate-async",
            headers=auth,
            json={"brief": "Queued run.", "regenerate": True},
        )
        queued = r.status_code == 202
        check.check(
            "POST",
            "/trips/{id}/generate-async",
            r,
            (202, 502, 503),
            "queued" if queued else "no broker - refused cleanly",
        )
        if queued:
            task_id = r.json()["task_id"]
            r = await client.get(f"{api}/trips/tasks/{task_id}", headers=auth)
            check.check("GET", "/trips/tasks/{task_id}", r, 200, r.json().get("state", ""))

        # ------------------------------------------------------------------
        section("Itineraries")
        r = await client.get(f"{api}/itineraries", headers=auth)
        check.check("GET", "/itineraries", r, 200, f"total={r.json()['meta']['total']}")

        r = await client.get(f"{api}/itineraries/{itinerary_id}", headers=auth)
        check.check("GET", "/itineraries/{id}", r, 200)

        r = await client.get(f"{api}/itineraries/{itinerary_id}/stats", headers=auth)
        check.check("GET", "/itineraries/{id}/stats", r, 200, str(r.json())[:110])

        # Reorder day 1, if it has enough activities to make it meaningful.
        target = next((d for d in days if len(d["activities"]) >= 2), None)
        if target:
            count = len(target["activities"])
            new_order = list(reversed(range(count)))
            r = await client.post(
                f"{api}/itineraries/{itinerary_id}/reorder",
                headers=auth,
                json={"day_number": target["day_number"], "activity_order": new_order},
            )
            check.check("POST", "/itineraries/{id}/reorder", r, 200)
            if r.status_code == 200:
                reordered = next(
                    d for d in r.json()["days"] if d["day_number"] == target["day_number"]
                )
                check.assertion(
                    "reorder applied the requested sequence",
                    [a["title"] for a in reordered["activities"]]
                    == [a["title"] for a in reversed(target["activities"])],
                )
                _verify_schedule(check, [reordered], label="after reorder")

            # An invalid permutation must be rejected.
            r = await client.post(
                f"{api}/itineraries/{itinerary_id}/reorder",
                headers=auth,
                json={"day_number": target["day_number"], "activity_order": [0, 0]},
            )
            check.check("POST", "/itineraries/{id}/reorder (bad permutation)", r, 422)

            r = await client.delete(
                f"{api}/itineraries/{itinerary_id}/days/"
                f"{target['day_number']}/activities/0",
                headers=auth,
            )
            check.check("DELETE", "/itineraries/{id}/days/{d}/activities/{i}", r, 200)
            if r.status_code == 200:
                after = next(
                    d for d in r.json()["days"] if d["day_number"] == target["day_number"]
                )
                check.assertion(
                    "activity removal reduced the count",
                    len(after["activities"]) == count - 1,
                    f"{count} -> {len(after['activities'])}",
                )

        r = await client.post(f"{api}/itineraries/{itinerary_id}/activate", headers=auth)
        check.check("POST", "/itineraries/{id}/activate", r, 200)

        # ------------------------------------------------------------------
        section("Standalone route optimisation")
        r = await client.get(
            f"{api}/places",
            params={"city": "Rome", "limit": 8},
        )
        rome = [p["id"] for p in r.json()["items"]] if r.status_code == 200 else []
        if len(rome) >= 4:
            r = await client.post(
                f"{api}/itineraries/optimize-route",
                headers=auth,
                json={
                    "place_ids": rome[:6],
                    "start_latitude": 41.9028,
                    "start_longitude": 12.4964,
                    "day_start_time": "09:00",
                    "day_end_time": "19:00",
                    "days": 2,
                    "transport_mode": "walking",
                    "max_places_per_day": 3,
                },
            )
            check.check("POST", "/itineraries/optimize-route", r, 200)
            if r.status_code == 200:
                data = r.json()
                print(
                    f"      {len(data['stops'])} stops, "
                    f"{data['distance_before_km']} -> {data['total_distance_km']} km "
                    f"({data['engine']}/{data['solver_status']})"
                )
                check.assertion(
                    "optimised route is no longer than the naive order",
                    data["distance_before_km"] is None
                    or data["total_distance_km"] <= data["distance_before_km"] + 0.001,
                    f"{data['distance_before_km']} -> {data['total_distance_km']}",
                )
                check.assertion(
                    "per-day stop cap respected",
                    all(
                        sum(1 for s in data["stops"] if s["day_number"] == d) <= 3
                        for d in {s["day_number"] for s in data["stops"]}
                    ),
                )

            r = await client.post(
                f"{api}/itineraries/optimize-route",
                headers=auth,
                json={
                    "place_ids": rome[:1],
                    "start_latitude": 41.9,
                    "start_longitude": 12.5,
                },
            )
            check.check("POST", "/itineraries/optimize-route (too few places)", r, 422)

        # ------------------------------------------------------------------
        section("Sharing")
        r = await client.post(f"{api}/trips/{trip_id}/share", headers=auth)
        check.check("POST", "/trips/{id}/share", r, 200)
        share_token = r.json().get("share_token") if r.status_code == 200 else None

        if share_token:
            r = await client.get(f"{api}/trips/shared/{share_token}")
            check.check("GET", "/trips/shared/{token} (no auth)", r, 200)

            r = await client.delete(f"{api}/trips/{trip_id}/share", headers=auth)
            check.check("DELETE", "/trips/{id}/share", r, 200)

            r = await client.get(f"{api}/trips/shared/{share_token}")
            check.check("GET", "/trips/shared/{token} (after unshare)", r, 404)

        # ------------------------------------------------------------------
        section("Ownership isolation")
        other_email = f"other-{suffix}@example.com"
        r = await client.post(
            f"{api}/auth/register",
            json={
                "email": other_email,
                "password": password,
                "full_name": "Other User",
            },
        )
        if r.status_code == 201:
            other_auth = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}
            r = await client.get(f"{api}/trips/{trip_id}", headers=other_auth)
            check.check("GET", "/trips/{id} (other user's trip)", r, 403)
            r = await client.get(f"{api}/itineraries/{itinerary_id}", headers=other_auth)
            check.check("GET", "/itineraries/{id} (other user's)", r, 403)

        # ------------------------------------------------------------------
        section("AI runs audit trail")
        r = await client.get(f"{api}/ai/runs", headers=auth, params={"limit": 5})
        runs = r.json() if r.status_code == 200 else []
        check.check("GET", "/ai/runs", r, 200, f"{len(runs)} runs")
        if runs:
            steps = [s["step"] for s in runs[0].get("steps", [])]
            check.assertion(
                "every pipeline stage was recorded",
                {
                    "parse_intent",
                    "retrieve_candidates",
                    "score_candidates",
                    "optimize_schedule",
                    "assemble_itinerary",
                    "narrate",
                    "validate_plan",
                }.issubset(set(steps)),
                ", ".join(steps),
            )

        # ------------------------------------------------------------------
        section("Admin")
        r = await client.post(
            f"{api}/auth/login",
            json={"email": "admin@example.com", "password": "adminpassword1"},
        )
        if check.check("POST", "/auth/login (admin)", r, 200):
            admin_auth = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}
            r = await client.get(f"{api}/health/details", headers=admin_auth)
            check.check("GET", "/health/details", r, 200)
            if r.status_code == 200:
                body = r.json()
                print(
                    f"      mongodb {body['mongodb']['server']['version']}, "
                    f"ortools={body['optimization']['ortools_available']}, "
                    f"collections={list(body['mongodb']['indexes'])}"
                )

            r = await client.get(f"{api}/users", headers=admin_auth, params={"limit": 5})
            check.check(
                "GET",
                "/users (admin)",
                r,
                200,
                f"total={r.json()['meta']['total']}",
            )

            new_place = {
                "name": f"Verification Place {suffix}",
                "description": "A place created by the verification script.",
                "category": "attraction",
                "tags": ["history"],
                "latitude": 48.8600,
                "longitude": 2.3300,
                "city": "Paris",
                "country": "France",
                "average_visit_minutes": 45,
                "rating": 4.1,
                "cost": {"amount": 8.0, "currency": "EUR"},
            }
            r = await client.post(f"{api}/places", headers=admin_auth, json=new_place)
            check.check("POST", "/places (admin)", r, 201)
            created_id = r.json().get("id") if r.status_code == 201 else None

            if created_id:
                r = await client.patch(
                    f"{api}/places/{created_id}",
                    headers=admin_auth,
                    json={"rating": 4.9, "average_visit_minutes": 50},
                )
                check.check("PATCH", "/places/{id} (admin)", r, 200)

                r = await client.patch(
                    f"{api}/places/{created_id}",
                    headers=admin_auth,
                    json={"latitude": 48.87},
                )
                check.check("PATCH", "/places/{id} (lone latitude)", r, 422)

                r = await client.delete(
                    f"{api}/places/{created_id}",
                    headers=admin_auth,
                    params={"hard": "true"},
                )
                check.check("DELETE", "/places/{id} (admin, hard)", r, 200)

            r = await client.post(
                f"{api}/places/admin/backfill-embeddings", headers=admin_auth
            )
            check.check(
                "POST",
                "/places/admin/backfill-embeddings",
                r,
                200,
                str(r.json().get("details")),
            )

        # ------------------------------------------------------------------
        section("Metrics")
        r = await client.get(f"{base_url.rstrip('/')}/metrics")
        body = r.text if r.status_code == 200 else ""
        check.check("GET", "/metrics", r, 200, f"{len(body.splitlines())} lines")
        check.assertion(
            "application metrics are exported",
            "itineraries_generated_total" in body and "optimizer_runs_total" in body,
        )

        r = await client.get(f"{base_url.rstrip('/')}/openapi.json")
        spec = r.json() if r.status_code == 200 else {}
        operations = sum(
            1
            for path in spec.get("paths", {}).values()
            for method in path
            if method in {"get", "post", "put", "patch", "delete"}
        )
        check.check("GET", "/openapi.json", r, 200, f"{operations} operations")

        # ------------------------------------------------------------------
        section("Cleanup")
        r = await client.delete(f"{api}/trips/{trip_id}", headers=auth)
        check.check("DELETE", "/trips/{id}", r, 200)

        r = await client.get(f"{api}/trips/{trip_id}", headers=auth)
        check.check("GET", "/trips/{id} (after delete)", r, 404)

        r = await client.post(
            f"{api}/auth/change-password",
            headers=auth,
            json={"current_password": password, "new_password": "newVerifyPass456"},
        )
        check.check("POST", "/auth/change-password", r, 200)

        r = await client.post(f"{api}/auth/refresh", json={"refresh_token": refresh})
        check.check("POST", "/auth/refresh (revoked by password change)", r, 401)

        r = await client.post(
            f"{api}/auth/login", json={"email": email, "password": "newVerifyPass456"}
        )
        check.check("POST", "/auth/login (new password)", r, 200)
        if r.status_code == 200:
            auth = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}
            fresh_refresh = r.json()["tokens"]["refresh_token"]

            r = await client.post(f"{api}/auth/logout-all", headers=auth)
            check.check("POST", "/auth/logout-all", r, 200)

            r = await client.post(
                f"{api}/auth/logout", json={"refresh_token": fresh_refresh}
            )
            check.check("POST", "/auth/logout", r, 200)

            r = await client.delete(f"{api}/users/me", headers=auth)
            check.check("DELETE", "/users/me", r, 200)

    return check.summary()


def _verify_schedule(checker: Checker, days: list[dict[str, Any]], label: str = "") -> None:
    """Assert that no day's activities overlap and every block is well-formed."""
    suffix = f" ({label})" if label else ""
    overlaps: list[str] = []
    malformed: list[str] = []

    for day in days:
        previous_end = -1
        for activity in day["activities"]:
            start = _minutes(activity["start_time"])
            end = _minutes(activity["end_time"])
            if end < start:
                malformed.append(f"day {day['day_number']}: {activity['title']}")
            if start < previous_end:
                overlaps.append(
                    f"day {day['day_number']}: {activity['title']} "
                    f"starts {activity['start_time']} before previous end"
                )
            previous_end = max(previous_end, end)

    checker.assertion(
        f"no overlapping activities{suffix}",
        not overlaps,
        "; ".join(overlaps[:3]) if overlaps else "clean",
    )
    checker.assertion(
        f"every activity ends after it starts{suffix}",
        not malformed,
        "; ".join(malformed[:3]) if malformed else "clean",
    )


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(main(args.base_url)))
    except httpx.ConnectError:
        print(f"Could not reach {args.base_url}. Is the server running?")
        sys.exit(2)
