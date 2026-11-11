"""Turn filter, jump, and cursor inputs into one collage page."""

from __future__ import annotations

from datetime import date

from app.grouping import drop_copies, photo_matches
from app.models import Photo, TimelineCriteria, TimelinePage
from app.search import (
    InvalidCursor,
    build_metadata_body,
    build_smart_body,
    criteria_from_cursor,
    cursor_payload,
    decode_cursor,
    encode_cursor,
)

SMART_LIMIT = 200
_EMPTY_HOPS = 8


def _sort(photos: list[Photo]) -> list[Photo]:
    return sorted(photos, key=lambda photo: (photo.taken_at, photo.id))


def _matching(
    photos: list[Photo],
    criteria: TimelineCriteria,
    *,
    jump: date | None,
    before: date | None,
) -> list[Photo]:
    return _sort(
        drop_copies([photo for photo in photos if photo_matches(photo, criteria, jump=jump, before=before)])
    )


class TimelineSource:
    """The slice of Immich this service needs. Tests pass a fake."""

    async def search_metadata(self, body: dict):
        raise NotImplementedError

    async def search_smart(self, body: dict):
        raise NotImplementedError

    async def date_bounds(self, criteria: TimelineCriteria):
        raise NotImplementedError


async def load_timeline_page(
    immich: TimelineSource,
    *,
    criteria: TimelineCriteria,
    edge: str,
    jump: date | None,
    cursor_token: str | None,
    limit: int,
) -> TimelinePage:
    before: date | None = None
    immich_cursor: str | None = None
    offer_older_than_jump = False

    if cursor_token:
        token = decode_cursor(cursor_token)
        criteria = criteria_from_cursor(token)
        direction = token["direction"]
        jump = _token_date(token, "jump")
        before = _token_date(token, "before")
        immich_cursor = token.get("immich") or None
    elif jump is not None:
        direction = "asc"
        offer_older_than_jump = True
    elif edge == "earliest":
        direction = "asc"
    else:
        direction = "desc"

    if criteria.text:
        body = build_smart_body(criteria, limit=SMART_LIMIT, jump=jump, before=before)
        page = await immich.search_smart(body)
        photos = _matching(page.photos, criteria, jump=jump, before=before)
        earliest, latest = await immich.date_bounds(criteria)
        return TimelinePage(photos, None, None, earliest, latest)

    photos, next_immich = await _collect(
        immich,
        criteria=criteria,
        direction=direction,
        jump=jump,
        before=before,
        immich_cursor=immich_cursor,
        limit=limit,
    )
    earliest, latest = await immich.date_bounds(criteria)
    continuation = _continue(
        criteria,
        direction=direction,
        jump=jump,
        before=before,
        immich_cursor=next_immich,
    )
    if direction == "desc":
        older = continuation
        newer = None
    else:
        older = None
        newer = continuation
    if offer_older_than_jump and jump is not None:
        older = encode_cursor(
            cursor_payload(
                criteria,
                direction="desc",
                jump=None,
                before=jump,
                immich_cursor=None,
            )
        )
    return TimelinePage(photos, older, newer, earliest, latest)


async def _collect(
    immich: TimelineSource,
    *,
    criteria: TimelineCriteria,
    direction: str,
    jump: date | None,
    before: date | None,
    immich_cursor: str | None,
    limit: int,
) -> tuple[list[Photo], str | None]:
    cursor = immich_cursor
    for _ in range(_EMPTY_HOPS):
        body = build_metadata_body(
            criteria,
            direction=direction,
            limit=limit,
            jump=jump,
            before=before,
            immich_cursor=cursor,
        )
        page = await immich.search_metadata(body)
        matched = _matching(page.photos, criteria, jump=jump, before=before)
        if matched or not page.next_cursor:
            return matched, page.next_cursor
        cursor = page.next_cursor
    return [], None


def _continue(
    criteria: TimelineCriteria,
    *,
    direction: str,
    jump: date | None,
    before: date | None,
    immich_cursor: str | None,
) -> str | None:
    if not immich_cursor:
        return None
    return encode_cursor(
        cursor_payload(
            criteria,
            direction=direction,
            jump=jump,
            before=before,
            immich_cursor=immich_cursor,
        )
    )


def _token_date(token: dict, key: str) -> date | None:
    raw = token.get(key)
    if not raw:
        return None
    if not isinstance(raw, str):
        raise InvalidCursor
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise InvalidCursor from exc
