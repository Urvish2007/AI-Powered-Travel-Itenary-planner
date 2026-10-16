"""Licensed city photographs from Wikimedia Commons.

For each city: Wikipedia's page summary names the article's lead image; Commons
says who made it and under what licence. A photo is kept only when all of
these hold:

* the file lives on Commons (files on a local wiki are often non-free);
* its licence is free - CC0, public domain, CC BY or CC BY-SA;
* the article's coordinates are within ``MAX_DISTANCE_KM`` of the city's own
  centre, so "Victoria" never comes back as a photo of a different Victoria;
* it is a photograph: a lead image is sometimes a locator map, a flag or a
  coat of arms, which would be a strange picture of a city.

Anything else is recorded as ``none``, and the card falls back to the curated
photo or the gradient.

The fetch is resumable: every city that was checked gets a record, so a rerun
asks only about cities never checked (``--retry-missing`` and ``--refresh``
widen that). It is polite and rate-limit aware: one request at a time with a
pause between them, a descriptive User-Agent as Wikimedia's policy asks, and
backoff on 429/503 that honours ``Retry-After``. A rate limit that persists
stops the run with its counts; nothing is lost.
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote, urlparse

import httpx

from app.core.config import settings
from app.models.city_photo import CityPhoto, PhotoStatus
from app.observability.logging import get_logger
from app.optimization.distance import Coordinate, haversine_km

logger = get_logger(__name__)

WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# Wide enough for a big city's article pin to sit away from our centroid of its
# sights; far tighter than the distance between two places sharing a name.
MAX_DISTANCE_KM = 50.0
THUMB_WIDTH = 1280

# Free licences only. Matched against Commons' LicenseShortName, lower-cased.
_FREE_LICENCE = re.compile(r"^(cc0|public domain|pd\b|pd-|cc by(-sa)? \d)")
_NON_FREE = re.compile(r"\b(nc|nd)\b|non-?commercial|no ?deriv")
_TAG = re.compile(r"<[^>]+>")
# Lead images that are not photographs: vector or animated formats, and the
# maps, flags and emblems that some city articles lead with.
_NOT_A_PHOTO = re.compile(
    r"\.(svg|gif|tiff?)$|map|locator|location|marker|flag|coat.of.arms|seal|logo|emblem",
    re.IGNORECASE,
)
# User-page link text Commons leaves around a username: "Yann ( talk )".
_USER_LINKS = re.compile(r"\(\s*(talk|contribs)(\s*\|\s*(talk|contribs))*\s*\)", re.I)

# Tests swap the network out by assigning an ``httpx.MockTransport`` here.
transport: httpx.AsyncBaseTransport | None = None


class PhotoFetchStopped(Exception):
    """The source kept refusing (rate limit, outage); stop and resume later."""


@dataclass(slots=True)
class PhotoReport:
    found: int = 0
    none: int = 0
    skipped: int = 0
    failed: int = 0
    stopped: str | None = None
    reasons: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CityRef:
    city: str
    country: str
    latitude: float | None
    longitude: float | None


def is_free_licence(short_name: str | None) -> bool:
    if not short_name:
        return False
    name = short_name.strip().lower()
    return bool(_FREE_LICENCE.match(name)) and not _NON_FREE.search(name)


def is_photograph(file_name: str) -> bool:
    return not _NOT_A_PHOTO.search(file_name)


def plain_text(value: str | None, limit: int = 200) -> str | None:
    """Commons metadata is HTML; keep the words, drop the markup."""
    if not value:
        return None
    text = " ".join(_USER_LINKS.sub(" ", html.unescape(_TAG.sub(" ", value))).split())
    return text[:limit] or None


def commons_file_name(image_url: str) -> str | None:
    """The Commons file name for an upload URL, or None if not on Commons."""
    path = urlparse(image_url).path
    if "/wikipedia/commons/" not in path:
        return None
    segments = path.split("/")
    if "thumb" in segments:
        # .../commons/thumb/a/ab/Name.jpg/1280px-Name.jpg -> Name.jpg
        return unquote(segments[segments.index("thumb") + 3])
    return unquote(segments[-1])


class _Client:
    """One polite, sequential Wikimedia session."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def get_json(self, url: str, params: dict[str, str] | None = None) -> Any:
        attempts = 1 + max(0, settings.photos_max_retries)
        for attempt in range(attempts):
            await asyncio.sleep(settings.photos_request_interval_seconds)
            try:
                response = await self.http.get(url, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == attempts - 1:
                    raise PhotoFetchStopped(
                        f"Wikimedia unreachable ({type(exc).__name__})"
                    ) from exc
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code == 404:
                return None
            if response.status_code in (429, 503):
                if attempt == attempts - 1:
                    raise PhotoFetchStopped(
                        f"Wikimedia is rate-limiting (HTTP {response.status_code})"
                    )
                await asyncio.sleep(_retry_after(response) or 2**attempt)
                continue
            response.raise_for_status()
            return response.json()
        raise PhotoFetchStopped("no attempts")  # pragma: no cover


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return min(60.0, float(response.headers.get("retry-after", "")))
    except ValueError:
        return None


async def find_photo(client: _Client, ref: CityRef) -> CityPhoto:
    """Look one city up. Returns a record either way; raises only to stop the run."""

    def none(reason: str) -> CityPhoto:
        return CityPhoto(
            city=ref.city, country=ref.country, status=PhotoStatus.NONE, reason=reason
        )

    summary = None
    for title in (ref.city, f"{ref.city}, {ref.country}"):
        candidate = await client.get_json(
            WIKIPEDIA_SUMMARY + quote(title.replace(" ", "_"), safe="")
        )
        if candidate and candidate.get("type") == "standard":
            summary = candidate
            break
    if summary is None:
        return none("no_article")

    pin = summary.get("coordinates") or {}
    if ref.latitude is not None and ref.longitude is not None:
        if "lat" not in pin or "lon" not in pin:
            return none("article_has_no_coordinates")
        distance = haversine_km(
            Coordinate(ref.latitude, ref.longitude), Coordinate(pin["lat"], pin["lon"])
        )
        if distance > MAX_DISTANCE_KM:
            return none("article_is_about_somewhere_else")

    original = (summary.get("originalimage") or {}).get("source")
    if not original:
        return none("article_has_no_image")
    file_name = commons_file_name(original)
    if file_name is None:
        return none("image_not_on_commons")
    if not is_photograph(file_name):
        return none("image_is_not_a_photo")

    info = await client.get_json(
        COMMONS_API,
        params={
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata",
            "iiurlwidth": str(THUMB_WIDTH),
            "titles": f"File:{file_name}",
        },
    )
    pages = (info or {}).get("query", {}).get("pages") or []
    image = (pages[0].get("imageinfo") or [None])[0] if pages else None
    if not image:
        return none("no_file_metadata")

    meta = image.get("extmetadata") or {}

    def value(key: str) -> str | None:
        entry = meta.get(key) or {}
        return entry.get("value") if isinstance(entry, dict) else None

    licence = plain_text(value("LicenseShortName"), 60)
    if not is_free_licence(licence):
        return none("licence_not_free")

    url = image.get("thumburl") or image.get("url")
    if not url:
        return none("no_file_url")
    # Commons appends utm_ tracking parameters; the image needs none of them.
    url = urlparse(url)._replace(query="").geturl()
    return CityPhoto(
        city=ref.city,
        country=ref.country,
        status=PhotoStatus.FOUND,
        image_url=url,
        width=image.get("thumbwidth") or image.get("width"),
        height=image.get("thumbheight") or image.get("height"),
        author=plain_text(value("Artist")),
        license=licence,
        license_url=value("LicenseUrl"),
        source_url=image.get("descriptionurl"),
    )


async def fetch_city_photos(
    cities: list[CityRef],
    *,
    refresh: bool = False,
    retry_missing: bool = False,
    limit: int | None = None,
) -> PhotoReport:
    """Find photos for ``cities``, skipping those already decided."""
    report = PhotoReport()
    known = {(p.city, p.country): p for p in await CityPhoto.find_all().to_list()}
    headers = {"User-Agent": settings.photos_user_agent}

    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(settings.photos_timeout_seconds),
        transport=transport,
        follow_redirects=True,
    ) as http:
        client = _Client(http)
        checked = 0
        for ref in cities:
            existing = known.get((ref.city, ref.country))
            decided = existing is not None and (
                existing.status is PhotoStatus.FOUND or not retry_missing
            )
            if decided and not refresh:
                report.skipped += 1
                continue
            if limit is not None and checked >= limit:
                break
            checked += 1
            try:
                photo = await find_photo(client, ref)
            except PhotoFetchStopped as exc:
                report.stopped = str(exc)
                break
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                # One odd reply should not end the run; the city is retried
                # next time because nothing was recorded for it.
                report.failed += 1
                logger.warning("city_photo_failed", city=ref.city, error=type(exc).__name__)
                continue

            if existing is not None:
                photo.id = existing.id
                photo.created_at = existing.created_at
            await photo.save()
            if photo.status is PhotoStatus.FOUND:
                report.found += 1
            else:
                report.none += 1
                reason = photo.reason or "unknown"
                report.reasons[reason] = report.reasons.get(reason, 0) + 1

    logger.info(
        "city_photos_fetched",
        found=report.found,
        none=report.none,
        skipped=report.skipped,
        failed=report.failed,
        stopped=report.stopped,
    )
    return report


# What the API exposes: the image and everything its licence asks us to show.
PUBLIC_FIELDS = (
    "image_url",
    "width",
    "height",
    "author",
    "license",
    "license_url",
    "source_url",
    "source",
)
