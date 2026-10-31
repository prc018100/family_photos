from datetime import date

from app.models import TimelineCriteria
from app.search import (
    InvalidCursor,
    build_metadata_body,
    build_smart_body,
    decode_cursor,
    encode_cursor,
    cursor_payload,
)
from tests.helpers import MOM


def _summer() -> TimelineCriteria:
    return TimelineCriteria(
        person_ids=(MOM,),
        start=date(2019, 6, 1),
        end=date(2019, 8, 31),
        text="beach",
    )


def test_mom_at_the_beach_uses_smart_search_with_structured_filters():
    body = build_smart_body(_summer(), limit=200, jump=None, before=None)

    assert body["query"] == "beach"
    assert body["size"] == 200
    assert "orderBy" not in body
    assert "page" not in body
    assert "personIds" not in body
    assert body["filter"]["personIds"] == {"all": [MOM]}
    assert body["filter"]["visibility"] == {"eq": "timeline"}
    assert body["filter"]["type"] == {"in": ["IMAGE", "VIDEO"]}
    # 36-hour pad around the local summer window, June 1 through August 31.
    assert body["filter"]["takenAt"] == {
        "gte": "2019-05-30T12:00:00.000Z",
        "lt": "2019-09-02T12:00:00.000Z",
    }


def test_metadata_body_uses_the_v3_filter_shape_and_local_order():
    criteria = TimelineCriteria(city="Santa Cruz", state="California", country="United States")
    body = build_metadata_body(
        criteria,
        direction="desc",
        limit=48,
        jump=date(2019, 7, 1),
        before=None,
        immich_cursor=None,
    )

    assert body["orderBy"] == {"field": "localDateTime", "direction": "desc"}
    assert body["withExif"] is True
    assert body["withPeople"] is True
    assert "cursor" not in body
    assert "city" not in body
    assert "takenAfter" not in body
    assert body["filter"]["city"] == {"eq": "Santa Cruz"}
    assert body["filter"]["state"] == {"eq": "California"}
    assert body["filter"]["country"] == {"eq": "United States"}
    assert body["filter"]["takenAt"]["gte"] == "2019-06-29T12:00:00.000Z"


def test_cursor_round_trip_keeps_filters_and_not_secrets():
    payload = cursor_payload(
        _summer(),
        direction="asc",
        jump=date(2019, 7, 1),
        before=None,
        immich_cursor="immich-cursor",
    )
    token = encode_cursor(payload)
    assert "beach" in decode_cursor(token)["q"]
    assert decode_cursor(token)["person_ids"] == [MOM]
    assert decode_cursor(token)["immich"] == "immich-cursor"
    assert "x-api-key" not in token

    try:
        decode_cursor("not-a-cursor")
    except InvalidCursor:
        pass
    else:
        raise AssertionError("expected InvalidCursor")
