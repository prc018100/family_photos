"""Values the timeline passes around. Dates are the photographer's local clock."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class PersonRef:
    id: str
    name: str


@dataclass(frozen=True)
class Photo:
    id: str
    taken_at: datetime
    city: str | None
    state: str | None
    country: str | None
    people: tuple[PersonRef, ...]
    filename: str
    type: str
    live: bool

    @property
    def taken_on(self) -> date:
        return self.taken_at.date()


@dataclass(frozen=True)
class DayGroup:
    day: date
    label: str
    weekday: str
    year: int
    month: int
    month_label: str
    show_year: bool
    show_month: bool
    photos: tuple[Photo, ...]

    @property
    def key(self) -> str:
        return self.day.isoformat()


@dataclass(frozen=True)
class TimelineCriteria:
    """Combined filters. Every set field is required (logical AND)."""

    person_ids: tuple[str, ...] = ()
    city: str | None = None
    state: str | None = None
    country: str | None = None
    start: date | None = None
    end: date | None = None
    text: str | None = None

    def cache_key(self) -> str:
        payload = {
            "person_ids": self.person_ids,
            "city": self.city,
            "state": self.state,
            "country": self.country,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "text": self.text,
        }
        return json.dumps(payload, sort_keys=True)


@dataclass(frozen=True)
class RawPage:
    photos: list[Photo]
    next_cursor: str | None


@dataclass(frozen=True)
class TimelinePage:
    photos: list[Photo]
    older_cursor: str | None
    newer_cursor: str | None
    earliest: date | None
    latest: date | None
