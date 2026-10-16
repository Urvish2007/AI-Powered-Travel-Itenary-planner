"""City photos: licence rules, wrong-city guard, resumability and the API field.

Wikimedia is replaced with an ``httpx.MockTransport``; nothing leaves the
machine. The database tests need MongoDB and skip without it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.services import photo_service
from app.services.photo_service import (
    CityRef,
    commons_file_name,
    fetch_city_photos,
    is_free_licence,
    is_photograph,
    plain_text,
)

ROME = CityRef("Rome", "Italy", 41.9, 12.49)
COMMONS_FILE = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Colosseum_in_Rome.jpg"


def summary(
    *,
    lat: float = 41.89,
    lon: float = 12.48,
    image: str | None = COMMONS_FILE,
    kind: str = "standard",
) -> dict[str, Any]:
    body: dict[str, Any] = {"type": kind, "coordinates": {"lat": lat, "lon": lon}}
    if image:
        body["originalimage"] = {"source": image, "width": 4000, "height": 3000}
    return body


def imageinfo(licence: str = "CC BY-SA 4.0", artist: str = "<a href='u'>Jane</a>") -> dict:
    return {
        "query": {
            "pages": [
                {
                    "imageinfo": [
                        {
                            "thumburl": "https://thumb.wikimedia.org/x/1280px-C.jpg?utm_source=a",
                            "thumbwidth": 1280,
                            "thumbheight": 960,
                            "descriptionurl": "https://commons.wikimedia.org/wiki/File:C.jpg",
                            "extmetadata": {
                                "LicenseShortName": {"value": licence},
                                "LicenseUrl": {"value": "https://creativecommons.org/x"},
                                "Artist": {"value": artist},
                            },
                        }
                    ]
                }
            ]
        }
    }


Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def wikimedia(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    """No pauses; yields the requests made. Tests install a handler with _serve."""
    monkeypatch.setattr(settings, "photos_request_interval_seconds", 0.0)

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(photo_service.asyncio, "sleep", instant)
    seen: list[httpx.Request] = []
    yield seen
    photo_service.transport = None


def _serve(seen: list[httpx.Request], handler: Handler) -> None:
    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    photo_service.transport = httpx.MockTransport(record)


def routes(*, summaries: dict[str, Any] | None = None, info: dict | None = None) -> Handler:
    """Answer the summary API per title, and the Commons API with ``info``."""
    summaries = summaries if summaries is not None else {"Rome": summary()}

    def handler(request: httpx.Request) -> httpx.Response:
        if "rest_v1/page/summary" in request.url.path:
            title = unquote(request.url.path.rsplit("/", 1)[-1]).replace("_", " ")
            body = summaries.get(title)
            return httpx.Response(200, json=body) if body else httpx.Response(404)
        return httpx.Response(200, json=info if info is not None else imageinfo())

    return handler


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("licence", "free"),
    [
        ("CC BY-SA 4.0", True),
        ("CC BY 2.0", True),
        ("CC0", True),
        ("Public domain", True),
        ("CC BY-NC 2.0", False),
        ("CC BY-NC-SA 3.0", False),
        ("CC BY-ND 4.0", False),
        ("Fair use", False),
        (None, False),
    ],
)
def test_only_free_licences_are_accepted(licence: str | None, free: bool) -> None:
    assert is_free_licence(licence) is free


def test_commons_file_names() -> None:
    assert commons_file_name(COMMONS_FILE) == "Colosseum_in_Rome.jpg"
    thumb = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/A%2CB.jpg/800px-A%2CB.jpg"
    assert commons_file_name(thumb) == "A,B.jpg"
    # Files on a local wiki are often non-free; they are not used.
    assert commons_file_name("https://upload.wikimedia.org/wikipedia/en/a/ab/X.jpg") is None


@pytest.mark.parametrize(
    ("name", "photo"),
    [
        ("Skyline_of_Edinburgh.jpg", True),
        ("Imagen_de_los_canales_en_Amsterdam.png", True),
        ("Bali_in_Indonesia_(special_marker).svg", False),
        ("Flag_of_Singapore.svg", False),
        ("Coat_of_arms_of_Vienna.png", False),
        ("Locator_map_Kyoto.png", False),
        ("Animated_tram.gif", False),
    ],
)
def test_maps_flags_and_emblems_are_not_city_photos(name: str, photo: bool) -> None:
    """Real lead images found by the first run: a map and three flags."""
    assert is_photograph(name) is photo


def test_author_markup_is_reduced_to_a_name() -> None:
    assert plain_text("<a href='u'>Yann</a> (<a>talk</a>)") == "Yann"
    assert plain_text("Caf&eacute; <b>owner</b>") == "Café owner"


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


@pytest.mark.db
class TestFetch:
    async def test_a_free_commons_photo_is_stored_with_attribution(
        self, database: Any, wikimedia: list[httpx.Request]
    ) -> None:
        from app.models.city_photo import CityPhoto, PhotoStatus

        _serve(wikimedia, routes())
        report = await fetch_city_photos([ROME])

        assert (report.found, report.none, report.failed) == (1, 0, 0)
        photo = await CityPhoto.find_one(CityPhoto.city == "Rome")
        assert photo.status is PhotoStatus.FOUND
        # Tracking parameters are dropped.
        assert photo.image_url == "https://thumb.wikimedia.org/x/1280px-C.jpg"
        assert photo.author == "Jane"
        assert photo.license == "CC BY-SA 4.0"
        assert photo.source_url == "https://commons.wikimedia.org/wiki/File:C.jpg"
        # Wikimedia's policy: identify the client.
        assert "AI-Itinerary-Planner" in wikimedia[0].headers["User-Agent"]

    @pytest.mark.parametrize(
        ("handler", "reason"),
        [
            (routes(info=imageinfo(licence="CC BY-NC 2.0")), "licence_not_free"),
            (
                routes(summaries={"Rome": summary(lat=-33.9, lon=151.2)}),
                "article_is_about_somewhere_else",
            ),
            (
                routes(
                    summaries={
                        "Rome": summary(
                            image="https://upload.wikimedia.org/wikipedia/en/a/ab/X.jpg"
                        )
                    }
                ),
                "image_not_on_commons",
            ),
            (routes(summaries={"Rome": summary(image=None)}), "article_has_no_image"),
            (routes(summaries={}), "no_article"),
        ],
    )
    async def test_unusable_photos_are_recorded_as_none(
        self,
        database: Any,
        wikimedia: list[httpx.Request],
        handler: Handler,
        reason: str,
    ) -> None:
        from app.models.city_photo import CityPhoto, PhotoStatus

        _serve(wikimedia, handler)
        report = await fetch_city_photos([ROME])

        assert report.none == 1 and report.reasons == {reason: 1}
        photo = await CityPhoto.find_one(CityPhoto.city == "Rome")
        assert photo.status is PhotoStatus.NONE and photo.image_url is None

    async def test_a_disambiguation_page_falls_back_to_city_and_country(
        self, database: Any, wikimedia: list[httpx.Request]
    ) -> None:
        _serve(
            wikimedia,
            routes(
                summaries={
                    "Rome": summary(kind="disambiguation"),
                    "Rome, Italy": summary(),
                }
            ),
        )
        assert (await fetch_city_photos([ROME])).found == 1

    async def test_reruns_skip_checked_cities_unless_asked(
        self, database: Any, wikimedia: list[httpx.Request]
    ) -> None:
        _serve(wikimedia, routes(summaries={}))
        await fetch_city_photos([ROME])
        calls = len(wikimedia)

        again = await fetch_city_photos([ROME])
        assert again.skipped == 1 and len(wikimedia) == calls  # no new requests

        _serve(wikimedia, routes())
        retried = await fetch_city_photos([ROME], retry_missing=True)
        assert retried.found == 1

    async def test_a_lasting_rate_limit_stops_the_run_with_its_counts(
        self, database: Any, wikimedia: list[httpx.Request], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.models.city_photo import CityPhoto

        monkeypatch.setattr(settings, "photos_max_retries", 1)
        _serve(wikimedia, lambda _r: httpx.Response(429, headers={"retry-after": "3"}))

        report = await fetch_city_photos([ROME, CityRef("Paris", "France", 48.85, 2.35)])

        assert report.stopped and "rate-limiting" in report.stopped
        assert len(wikimedia) == 2  # one try and one retry, then stop
        # Nothing recorded, so the next run asks again.
        assert await CityPhoto.count() == 0

    async def test_a_transient_rate_limit_is_waited_out(
        self, database: Any, wikimedia: list[httpx.Request]
    ) -> None:
        healthy = routes()
        replies = iter([httpx.Response(429)])

        def flaky(request: httpx.Request) -> httpx.Response:
            return next(replies, None) or healthy(request)

        _serve(wikimedia, flaky)
        assert (await fetch_city_photos([ROME])).found == 1


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_city_summaries_carry_the_photo_and_its_attribution(
    client: AsyncClient, seeded: dict[str, int], wikimedia: list[httpx.Request]
) -> None:
    _serve(wikimedia, routes())
    await fetch_city_photos([ROME])

    response = await client.get("/api/v1/places/cities")
    assert response.status_code == 200
    by_city = {c["city"]: c for c in response.json()}

    photo = by_city["Rome"]["photo"]
    assert photo["image_url"].startswith("https://thumb.wikimedia.org/")
    assert photo["author"] == "Jane"
    assert photo["license"] == "CC BY-SA 4.0"
    assert photo["license_url"] and photo["source_url"]
    # A city without one says so plainly, for the gradient fallback.
    assert by_city["Paris"]["photo"] is None
