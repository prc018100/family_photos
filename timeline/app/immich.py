"""HTTP client for the Immich API. The API key stays in this process."""

from __future__ import annotations

import logging
import time
from datetime import date
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from app.config import Settings
from app.grouping import parse_local_datetime
from app.models import PersonRef, Photo, RawPage, TimelineCriteria
from app.search import build_metadata_body

logger = logging.getLogger("timeline.immich")

_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_MAX_IMAGE_BYTES = 15 * 1024 * 1024


class ImmichError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _place(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# EXIF orientations that turn the sensor frame on its side, in the spellings
# Immich has stored over the years.
_SIDEWAYS = {"5", "6", "7", "8", "90", "-90", "270", "-270"}


def _positive(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def display_ratio(raw: dict, exif: dict) -> float | None:
    """Width over height the way the photo is shown, or None when unknown."""

    width = _positive(exif.get("exifImageWidth"))
    height = _positive(exif.get("exifImageHeight"))
    if width and height:
        if str(exif.get("orientation") or "").strip() in _SIDEWAYS:
            width, height = height, width
    else:
        width = _positive(raw.get("width"))
        height = _positive(raw.get("height"))
    if not width or not height:
        return None
    return round(min(4.0, max(0.25, width / height)), 4)


def parse_asset(raw: dict) -> Photo | None:
    asset_id = raw.get("id")
    if not isinstance(asset_id, str) or not asset_id:
        return None
    kind = raw.get("type") or "IMAGE"
    if kind not in {"IMAGE", "VIDEO"}:
        return None
    taken = parse_local_datetime(raw.get("localDateTime")) or parse_local_datetime(raw.get("fileCreatedAt"))
    if taken is None:
        return None
    exif = raw.get("exifInfo") or {}
    if not isinstance(exif, dict):
        exif = {}
    people: list[PersonRef] = []
    for person in raw.get("people") or []:
        if not isinstance(person, dict) or not person.get("id"):
            continue
        people.append(PersonRef(id=str(person["id"]), name=str(person.get("name") or "")))
    people.sort(key=lambda person: (person.name.casefold(), person.id))
    return Photo(
        id=asset_id,
        taken_at=taken,
        city=_place(exif.get("city")),
        state=_place(exif.get("state")),
        country=_place(exif.get("country")),
        people=tuple(people),
        filename=str(raw.get("originalFileName") or ""),
        type=str(kind),
        live=bool(raw.get("livePhotoVideoId")),
        ratio=display_ratio(raw, exif),
    )


def parse_search_response(payload: dict) -> RawPage:
    assets = payload.get("assets") or {}
    if not isinstance(assets, dict):
        raise ImmichError("Immich returned an unexpected search payload")
    photos: list[Photo] = []
    for item in assets.get("items") or []:
        if not isinstance(item, dict):
            continue
        photo = parse_asset(item)
        if photo is not None:
            photos.append(photo)
    cursor = assets.get("nextCursor")
    if not isinstance(cursor, str) or not cursor:
        cursor = None
    return RawPage(photos=photos, next_cursor=cursor)


def rejects_image_target(url: str) -> bool:
    """True when a URL would fetch the original file or a full-size rendition."""

    parsed = urlparse(url)
    if parsed.path.rstrip("/").endswith("/original"):
        return True
    sizes = [item.lower() for item in parse_qs(parsed.query).get("size", [])]
    return any(size in {"original", "fullsize"} for size in sizes)


class ImmichClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._base = settings.immich_url.rstrip("/")
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._bounds: dict[str, tuple[float, tuple[date | None, date | None]]] = {}

    async def open(self) -> None:
        headers = {"Accept": "application/json"}
        if self._settings.immich_api_key:
            headers["x-api-key"] = self._settings.immich_api_key
        self._http = httpx.AsyncClient(
            timeout=self._settings.timeout,
            headers=headers,
            transport=self._transport,
            follow_redirects=False,
        )

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise ImmichError("Immich client is not open")
        return self._http

    def _url(self, path: str) -> str:
        return self._base + path

    def _allowed(self, url: str) -> bool:
        target = urlparse(url)
        allowed = urlparse(self._base)
        return target.scheme in {"http", "https"} and target.netloc == allowed.netloc

    async def _send(self, method: str, path: str, *, json_body: dict | None = None, params: dict | None = None):
        try:
            response = await self.http.request(method, self._url(path), json=json_body, params=params)
        except httpx.HTTPError as exc:
            logger.warning("Immich %s %s failed: %s", method, path, type(exc).__name__)
            raise ImmichError("Immich is not reachable") from exc
        if response.status_code == 401:
            raise ImmichError("Immich rejected the API key", status_code=401)
        if response.status_code == 403:
            raise ImmichError("The API key is missing a permission Immich requires", status_code=403)
        if response.status_code >= 400:
            logger.warning("Immich %s %s returned %s", method, path, response.status_code)
            raise ImmichError(f"Immich returned {response.status_code}", status_code=response.status_code)
        return response

    async def search_metadata(self, body: dict) -> RawPage:
        response = await self._send("POST", "/api/search/metadata", json_body=body)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ImmichError("Immich returned an unexpected search payload")
        return parse_search_response(payload)

    async def search_smart(self, body: dict) -> RawPage:
        response = await self._send("POST", "/api/search/smart", json_body=body)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ImmichError("Immich returned an unexpected search payload")
        return parse_search_response(payload)

    async def date_bounds(self, criteria: TimelineCriteria) -> tuple[date | None, date | None]:
        # Word search ranks a subset. The scrubber still spans the structured
        # filters (person, place, dates), which do not include that text.
        structured = TimelineCriteria(
            person_ids=criteria.person_ids,
            city=criteria.city,
            state=criteria.state,
            country=criteria.country,
            start=criteria.start,
            end=criteria.end,
        )
        key = structured.cache_key()
        cached = self._bounds.get(key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < 120:
            return cached[1]
        earliest = await self._edge_date(structured, "asc")
        latest = await self._edge_date(structured, "desc")
        result = (earliest, latest)
        self._bounds[key] = (now, result)
        return result

    async def _edge_date(self, criteria: TimelineCriteria, direction: str) -> date | None:
        body = build_metadata_body(
            criteria,
            direction=direction,
            limit=1,
            jump=None,
            before=None,
            immich_cursor=None,
        )
        page = await self.search_metadata(body)
        if not page.photos:
            return None
        return page.photos[0].taken_on

    async def list_people(self) -> list[PersonRef]:
        found: list[PersonRef] = []
        page = 1
        while page <= 40:
            response = await self._send(
                "GET",
                "/api/people",
                params={"page": page, "size": 1000, "withHidden": "false"},
            )
            payload = response.json()
            if not isinstance(payload, dict):
                raise ImmichError("Immich returned an unexpected people payload")
            for raw in payload.get("people") or []:
                if not isinstance(raw, dict) or not raw.get("id"):
                    continue
                found.append(PersonRef(id=str(raw["id"]), name=str(raw.get("name") or "")))
            if not payload.get("hasNextPage"):
                break
            page += 1
        return sorted(found, key=lambda person: (not person.name.strip(), person.name.casefold(), person.id))

    async def list_places(self) -> dict[str, list[str]]:
        collected: dict[str, list[str]] = {}
        for kind, label in (("city", "cities"), ("state", "states"), ("country", "countries")):
            response = await self._send("GET", "/api/search/suggestions", params={"type": kind})
            payload = response.json()
            names: list[str] = []
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, str) and item.strip():
                        names.append(item.strip())
            collected[label] = sorted(set(names), key=str.casefold)
        return collected

    async def get_asset(self, asset_id: str) -> Photo:
        response = await self._send("GET", f"/api/assets/{asset_id}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ImmichError("Immich returned an unexpected asset payload")
        photo = parse_asset(payload)
        if photo is None:
            raise ImmichError("That asset has no date the timeline can show")
        return photo

    async def fetch_image(self, kind: str, entity_id: str, size: str | None = None) -> tuple[str, bytes]:
        if kind == "asset":
            if size not in {"thumbnail", "preview"}:
                raise ImmichError("The timeline only requests thumbnail or preview images")
            url = self._url(f"/api/assets/{entity_id}/thumbnail")
            params: dict | None = {"size": size}
        elif kind == "person":
            url = self._url(f"/api/people/{entity_id}/thumbnail")
            params = None
        else:
            raise ImmichError("Unknown image request")

        for _ in range(3):
            if not self._allowed(url) or rejects_image_target(url):
                raise ImmichError("Refusing to proxy that image")
            try:
                response = await self.http.get(url, params=params)
            except httpx.HTTPError as exc:
                logger.warning("Immich image request failed: %s", type(exc).__name__)
                raise ImmichError("Immich is not reachable") from exc
            try:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location", "")
                    if not location:
                        raise ImmichError("Immich redirected without a destination")
                    url = urljoin(str(response.url), location)
                    params = None
                    if not self._allowed(url) or rejects_image_target(url):
                        raise ImmichError("Refusing to proxy an original file")
                    continue
                if response.status_code != 200:
                    raise ImmichError(
                        f"Immich image request failed ({response.status_code})",
                        status_code=response.status_code,
                    )
                data = bytes(response.content)
                if len(data) > _MAX_IMAGE_BYTES:
                    raise ImmichError("Immich preview was larger than the timeline allows")
                media_type = response.headers.get("content-type", "image/jpeg").split(";", 1)[0].strip().lower()
                if media_type not in _IMAGE_TYPES:
                    media_type = _sniff_image(data)
                return media_type, data
            finally:
                await response.aclose()
        raise ImmichError("Immich redirected too many times")


def _sniff_image(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    raise ImmichError("Immich did not return an image")
