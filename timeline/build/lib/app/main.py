"""Family photo timeline. Immich stays on the server side of these routes."""

from __future__ import annotations

import base64
import logging
import re
import secrets
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.immich import ImmichClient, ImmichError
from app.models import TimelineCriteria
from app.present import present_groups, present_photo
from app.search import InvalidCursor
from app.service import load_timeline_page

logger = logging.getLogger("timeline")
PACKAGE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=PACKAGE / "templates")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_IMAGE_CACHE = "private, max-age=86400"
_NO_STORE = "no-store"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if getattr(app.state, "immich", None) is None:
        client = ImmichClient(app.state.settings)
        await client.open()
        app.state.immich = client
        app.state.immich_owned = True
    else:
        app.state.immich_owned = False
    yield
    if app.state.immich_owned:
        await app.state.immich.close()


def create_app(settings: Settings | None = None, immich: ImmichClient | None = None) -> FastAPI:
    app = FastAPI(title="Family Photos", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings or Settings.from_env()
    app.state.immich = immich

    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        path = request.url.path
        if path == "/healthz" or path.startswith("/static/"):
            return await call_next(request)
        if not _authorized(request):
            return Response(
                "Authentication required",
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Basic realm="Family Photos"',
                    "Cache-Control": _NO_STORE,
                },
                media_type="text/plain",
            )
        return await call_next(request)

    @app.exception_handler(ImmichError)
    async def immich_failed(_request: Request, exc: ImmichError):
        logger.warning("Immich request failed: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=502, headers={"Cache-Control": _NO_STORE})

    @app.exception_handler(InvalidCursor)
    async def bad_cursor(_request: Request, _exc: InvalidCursor):
        return JSONResponse(
            {"detail": "That timeline position is no longer valid. Reload the page."},
            status_code=400,
            headers={"Cache-Control": _NO_STORE},
        )

    @app.get("/healthz")
    async def healthz(request: Request):
        configured = "configured" if request.app.state.settings.immich_api_key else "unconfigured"
        return JSONResponse({"status": "ok", "immich": configured})

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        settings_ = request.app.state.settings
        page = templates.TemplateResponse(
            request,
            "index.html",
            {
                "boot": {
                    "configured": bool(settings_.immich_api_key),
                    "publicUrl": settings_.immich_public_url,
                }
            },
        )
        page.headers["Cache-Control"] = _NO_STORE
        return page

    @app.get("/api/filters")
    async def filters(request: Request):
        _require_key(request)
        client = request.app.state.immich
        people = await client.list_people()
        places = await client.list_places()
        return JSONResponse(
            {
                "people": [
                    {
                        "id": person.id,
                        "name": person.name,
                        "thumbnail_url": f"/media/people/{person.id}",
                    }
                    for person in people
                ],
                "cities": places["cities"],
                "states": places["states"],
                "countries": places["countries"],
            },
            headers={"Cache-Control": _NO_STORE},
        )

    @app.get("/api/timeline")
    async def timeline(
        request: Request,
        person_id: list[str] | None = Query(default=None),
        city: str | None = None,
        state: str | None = None,
        country: str | None = None,
        start: date | None = None,
        end: date | None = None,
        q: str | None = None,
        jump: date | None = None,
        edge: str = "latest",
        cursor: str | None = None,
    ):
        _require_key(request)
        if edge not in {"latest", "earliest"}:
            raise HTTPException(status_code=400, detail="edge must be latest or earliest")
        criteria = _criteria(person_id, city, state, country, start, end, q)
        if criteria.start and criteria.end and criteria.start > criteria.end:
            raise HTTPException(status_code=400, detail="The start date is after the end date")
        page = await load_timeline_page(
            request.app.state.immich,
            criteria=criteria,
            edge=edge,
            jump=jump,
            cursor_token=cursor,
            limit=request.app.state.settings.page_size,
        )
        public = request.app.state.settings.immich_public_url
        return JSONResponse(
            {
                "groups": present_groups(page.photos, public),
                "older_cursor": page.older_cursor,
                "newer_cursor": page.newer_cursor,
                "bounds": {
                    "earliest": page.earliest.isoformat() if page.earliest else None,
                    "latest": page.latest.isoformat() if page.latest else None,
                },
            },
            headers={"Cache-Control": _NO_STORE},
        )

    @app.get("/api/assets/{asset_id}")
    async def asset_detail(asset_id: str, request: Request):
        _require_key(request)
        _require_uuid(asset_id)
        photo = await request.app.state.immich.get_asset(asset_id)
        return JSONResponse(
            present_photo(photo, request.app.state.settings.immich_public_url),
            headers={"Cache-Control": _NO_STORE},
        )

    @app.get("/media/assets/{asset_id}")
    async def asset_media(asset_id: str, request: Request, size: str = "thumbnail"):
        _require_key(request)
        _require_uuid(asset_id)
        if size not in {"thumbnail", "preview"}:
            raise HTTPException(status_code=400, detail="size must be thumbnail or preview")
        media_type, data = await request.app.state.immich.fetch_image("asset", asset_id, size)
        return _image(media_type, data)

    @app.get("/media/people/{person_id}")
    async def person_media(person_id: str, request: Request):
        _require_key(request)
        _require_uuid(person_id)
        media_type, data = await request.app.state.immich.fetch_image("person", person_id, None)
        return _image(media_type, data)

    return app


def _image(media_type: str, data: bytes) -> Response:
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": _IMAGE_CACHE, "X-Content-Type-Options": "nosniff"},
    )


def _require_key(request: Request) -> None:
    if not request.app.state.settings.immich_api_key:
        raise HTTPException(
            status_code=503,
            detail="Set IMMICH_API_KEY in the environment and recreate the timeline container.",
        )


def _require_uuid(value: str) -> None:
    if not _UUID.fullmatch(value):
        raise HTTPException(status_code=400, detail="Invalid id")


def _clean(value: str | None, *, limit: int, label: str) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > limit:
        raise HTTPException(status_code=400, detail=f"{label} is too long")
    if any(ord(char) < 32 for char in text):
        raise HTTPException(status_code=400, detail=f"{label} is invalid")
    return text


def _criteria(
    person_ids: list[str] | None,
    city: str | None,
    state: str | None,
    country: str | None,
    start: date | None,
    end: date | None,
    text: str | None,
) -> TimelineCriteria:
    cleaned_ids: list[str] = []
    for person_id in person_ids or []:
        if not _UUID.fullmatch(person_id):
            raise HTTPException(status_code=400, detail="Invalid person id")
        if person_id not in cleaned_ids:
            cleaned_ids.append(person_id)
    if len(cleaned_ids) > 20:
        raise HTTPException(status_code=400, detail="Choose 20 people or fewer")
    return TimelineCriteria(
        person_ids=tuple(sorted(cleaned_ids)),
        city=_clean(city, limit=120, label="City"),
        state=_clean(state, limit=120, label="State"),
        country=_clean(country, limit=120, label="Country"),
        start=start,
        end=end,
        text=_clean(text, limit=200, label="Search"),
    )


def _authorized(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    if not settings.basic_auth_enabled:
        return True
    header = request.headers.get("authorization", "")
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    user, separator, password = decoded.partition(":")
    if not separator:
        return False
    return secrets.compare_digest(user.encode(), settings.basic_user.encode()) and secrets.compare_digest(
        password.encode(), settings.basic_password.encode()
    )


app = create_app()
