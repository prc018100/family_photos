"""JSON the browser is allowed to see. Image URLs point at this service."""

from __future__ import annotations

from app.grouping import group_photos
from app.models import Photo


def present_photo(photo: Photo, public_url: str) -> dict:
    link = f"{public_url}/photos/{photo.id}" if public_url else None
    return {
        "id": photo.id,
        "taken_at": photo.taken_at.strftime("%Y-%m-%dT%H:%M:%S"),
        "taken_on": photo.taken_on.isoformat(),
        "city": photo.city,
        "state": photo.state,
        "country": photo.country,
        "people": [{"id": person.id, "name": person.name} for person in photo.people],
        "filename": photo.filename,
        "type": photo.type,
        "live": photo.live,
        "ratio": photo.ratio,
        "thumbnail_url": f"/media/assets/{photo.id}?size=thumbnail",
        "preview_url": f"/media/assets/{photo.id}?size=preview",
        "immich_url": link,
    }


def present_groups(photos: list[Photo], public_url: str) -> list[dict]:
    groups = []
    for group in group_photos(photos):
        groups.append(
            {
                "key": group.key,
                "label": group.label,
                "weekday": group.weekday,
                "year": group.year,
                "month": group.month,
                "month_label": group.month_label,
                "show_year": group.show_year,
                "show_month": group.show_month,
                "photos": [present_photo(photo, public_url) for photo in group.photos],
            }
        )
    return groups
