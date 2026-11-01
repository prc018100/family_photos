"""Group photos into days and decide which photos a filter keeps.

Immich stores ``localDateTime`` as the photographer's wall clock with a trailing
``Z``. The ``Z`` is not a UTC conversion. Grouping uses those clock components
so "July 14" means the day the photo was taken where the camera was.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from app.models import DayGroup, Photo, TimelineCriteria

_LOCAL = re.compile(
    r"(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})[T ](?P<H>\d{2}):(?P<M>\d{2}):(?P<S>\d{2})"
)


def parse_local_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    match = _LOCAL.search(value)
    if match is None:
        return None
    return datetime(
        int(match["y"]),
        int(match["m"]),
        int(match["d"]),
        int(match["H"]),
        int(match["M"]),
        int(match["S"]),
    )


def effective_local_window(
    criteria: TimelineCriteria,
    *,
    jump: date | None = None,
    before: date | None = None,
) -> tuple[date | None, date | None]:
    """Inclusive local start and end after jump/before are applied.

    ``jump`` starts a window on that day. ``before`` ends it on the previous
    day, which is how the strip loads photos older than a jumped-to date
    without repeating that date.
    """

    start = criteria.start
    end = criteria.end
    if jump is not None and (start is None or jump > start):
        start = jump
    if before is not None:
        last = before - timedelta(days=1)
        if end is None or last < end:
            end = last
    return start, end


def photo_matches(
    photo: Photo,
    criteria: TimelineCriteria,
    *,
    jump: date | None = None,
    before: date | None = None,
) -> bool:
    """True when the photo satisfies every structured filter at once.

    Person filters use AND: choosing Mom and Dad keeps photos of both.
    A free-text smart-search query is not applied here. Immich ranks that.
    """

    if criteria.person_ids:
        present = {person.id for person in photo.people}
        if not set(criteria.person_ids).issubset(present):
            return False
    if criteria.city is not None and photo.city != criteria.city:
        return False
    if criteria.state is not None and photo.state != criteria.state:
        return False
    if criteria.country is not None and photo.country != criteria.country:
        return False
    start, end = effective_local_window(criteria, jump=jump, before=before)
    if start is not None and end is not None and start > end:
        return False
    if start is not None and photo.taken_on < start:
        return False
    if end is not None and photo.taken_on > end:
        return False
    return True


def group_photos(photos: Sequence[Photo]) -> list[DayGroup]:
    """Bucket photos by local day, oldest day first, stable within a day."""

    ordered = sorted(photos, key=lambda photo: (photo.taken_at, photo.id))
    seen: set[str] = set()
    unique: list[Photo] = []
    for photo in ordered:
        if photo.id in seen:
            continue
        seen.add(photo.id)
        unique.append(photo)

    buckets: dict[date, list[Photo]] = {}
    day_order: list[date] = []
    for photo in unique:
        if photo.taken_on not in buckets:
            buckets[photo.taken_on] = []
            day_order.append(photo.taken_on)
        buckets[photo.taken_on].append(photo)

    groups: list[DayGroup] = []
    previous_year: int | None = None
    previous_month: int | None = None
    for day in day_order:
        show_year = day.year != previous_year
        show_month = show_year or day.month != previous_month
        groups.append(
            DayGroup(
                day=day,
                label=f"{calendar.month_name[day.month]} {day.day}, {day.year}",
                weekday=calendar.day_name[day.weekday()],
                year=day.year,
                month=day.month,
                month_label=f"{calendar.month_name[day.month]} {day.year}",
                show_year=show_year,
                show_month=show_month,
                photos=tuple(buckets[day]),
            )
        )
        previous_year = day.year
        previous_month = day.month
    return groups
