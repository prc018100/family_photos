"""Build Immich v3.3 search bodies and opaque timeline cursors.

Immich's ``takenAt`` filter compares ``fileCreatedAt`` (a UTC instant), while
the collage groups by local wall-clock time. The query is padded by 36 hours
so a photo taken near midnight is not dropped, and ``photo_matches`` then
keeps only the requested local days.
"""

from __future__ import annotations

import base64
import json
from datetime import date, datetime, time, timedelta

from app.grouping import effective_local_window
from app.models import TimelineCriteria

PAD = timedelta(hours=36)


class InvalidCursor(ValueError):
    """The client sent a timeline cursor this server cannot continue."""


def _iso_z(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def taken_at_filter(
    criteria: TimelineCriteria,
    *,
    jump: date | None,
    before: date | None,
) -> dict[str, str] | None:
    start, end = effective_local_window(criteria, jump=jump, before=before)
    if start is None and end is None:
        return None
    if start is not None and end is not None and start > end:
        # Impossible window. Ask Immich for an empty instant so the page stops.
        point = _iso_z(datetime.combine(start, time.min))
        return {"gte": point, "lt": point}
    window: dict[str, str] = {}
    if start is not None:
        window["gte"] = _iso_z(datetime.combine(start, time.min) - PAD)
    if end is not None:
        exclusive_end = datetime.combine(end + timedelta(days=1), time.min) + PAD
        window["lt"] = _iso_z(exclusive_end)
    return window


def build_filter(
    criteria: TimelineCriteria,
    *,
    jump: date | None,
    before: date | None,
) -> dict:
    # Timeline visibility hides the video half of a Live Photo, so the still
    # shows once. Trash, archive, and locked items stay out of the collage.
    payload: dict = {
        "visibility": {"eq": "timeline"},
        "type": {"in": ["IMAGE", "VIDEO"]},
    }
    if criteria.person_ids:
        payload["personIds"] = {"all": list(criteria.person_ids)}
    if criteria.city is not None:
        payload["city"] = {"eq": criteria.city}
    if criteria.state is not None:
        payload["state"] = {"eq": criteria.state}
    if criteria.country is not None:
        payload["country"] = {"eq": criteria.country}
    taken = taken_at_filter(criteria, jump=jump, before=before)
    if taken is not None:
        payload["takenAt"] = taken
    return payload


def build_metadata_body(
    criteria: TimelineCriteria,
    *,
    direction: str,
    limit: int,
    jump: date | None,
    before: date | None,
    immich_cursor: str | None,
) -> dict:
    body: dict = {
        "size": limit,
        "withExif": True,
        "withPeople": True,
        "orderBy": {"field": "localDateTime", "direction": direction},
        "filter": build_filter(criteria, jump=jump, before=before),
    }
    if immich_cursor:
        body["cursor"] = immich_cursor
    return body


def build_smart_body(
    criteria: TimelineCriteria,
    *,
    limit: int,
    jump: date | None,
    before: date | None,
) -> dict:
    return {
        "size": limit,
        "query": criteria.text or "",
        "withExif": True,
        "filter": build_filter(criteria, jump=jump, before=before),
    }


def _date_token(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _date_from_token(value: object) -> date | None:
    if not value:
        return None
    if not isinstance(value, str):
        raise InvalidCursor
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidCursor from exc


def cursor_payload(
    criteria: TimelineCriteria,
    *,
    direction: str,
    jump: date | None,
    before: date | None,
    immich_cursor: str | None,
) -> dict:
    if direction not in {"asc", "desc"}:
        raise InvalidCursor
    return {
        "v": 1,
        "direction": direction,
        "immich": immich_cursor,
        "jump": _date_token(jump),
        "before": _date_token(before),
        "person_ids": list(criteria.person_ids),
        "city": criteria.city,
        "state": criteria.state,
        "country": criteria.country,
        "start": _date_token(criteria.start),
        "end": _date_token(criteria.end),
        "q": criteria.text or "",
    }


def encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(token: str) -> dict:
    if not token or len(token) > 4000:
        raise InvalidCursor
    padded = token + "=" * (-len(token) % 4)
    try:
        parsed = json.loads(base64.urlsafe_b64decode(padded))
    except (json.JSONDecodeError, ValueError) as exc:
        raise InvalidCursor from exc
    if not isinstance(parsed, dict) or parsed.get("v") != 1:
        raise InvalidCursor
    if parsed.get("direction") not in {"asc", "desc"}:
        raise InvalidCursor
    immich = parsed.get("immich")
    if immich is not None and not isinstance(immich, str):
        raise InvalidCursor
    return parsed


def criteria_from_cursor(token: dict) -> TimelineCriteria:
    people = token.get("person_ids") or []
    if not isinstance(people, list) or not all(isinstance(item, str) for item in people):
        raise InvalidCursor
    text = token.get("q") or None
    if text is not None and not isinstance(text, str):
        raise InvalidCursor

    def place(key: str) -> str | None:
        value = token.get(key)
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise InvalidCursor
        return value

    return TimelineCriteria(
        person_ids=tuple(people),
        city=place("city"),
        state=place("state"),
        country=place("country"),
        start=_date_from_token(token.get("start")),
        end=_date_from_token(token.get("end")),
        text=text or None,
    )
