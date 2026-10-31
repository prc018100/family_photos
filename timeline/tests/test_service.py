import asyncio
from datetime import date, datetime

from app.models import RawPage, TimelineCriteria
from app.search import decode_cursor
from app.service import load_timeline_page
from tests.helpers import MOM, photo


class FakeImmich:
    def __init__(self, pages):
        self.pages = list(pages)
        self.bodies = []
        self.smart = []

    async def search_metadata(self, body):
        self.bodies.append(body)
        return self.pages.pop(0)

    async def search_smart(self, body):
        self.smart.append(body)
        return self.pages.pop(0)

    async def date_bounds(self, criteria):
        return (date(2019, 6, 1), date(2019, 8, 31))


def _page(*photos, cursor):
    return RawPage(list(photos), cursor)


def test_latest_edge_walks_older_and_sorts_chronologically():
    asyncio.run(_latest_edge())


async def _latest_edge():
    newer = photo(id="newer", taken_at=datetime(2019, 8, 2, 12, 0))
    older = photo(id="older", taken_at=datetime(2019, 8, 1, 12, 0))
    fake = FakeImmich([_page(newer, older, cursor="older-page")])

    page = await load_timeline_page(
        fake,
        criteria=TimelineCriteria(),
        edge="latest",
        jump=None,
        cursor_token=None,
        limit=48,
    )

    assert [item.id for item in page.photos] == ["older", "newer"]
    assert fake.bodies[0]["orderBy"]["direction"] == "desc"
    assert page.newer_cursor is None
    assert decode_cursor(page.older_cursor)["immich"] == "older-page"
    assert decode_cursor(page.older_cursor)["direction"] == "desc"
    assert page.earliest == date(2019, 6, 1)
    assert page.latest == date(2019, 8, 31)


def test_empty_immich_page_is_skipped_until_a_local_day_matches():
    asyncio.run(_skip_empty_page())


async def _skip_empty_page():
    outside = photo(id="may", taken_at=datetime(2019, 5, 1, 12, 0))
    inside = photo(id="july", taken_at=datetime(2019, 7, 4, 12, 0))
    fake = FakeImmich(
        [
            _page(outside, cursor="next"),
            _page(inside, cursor="after-july"),
        ]
    )
    criteria = TimelineCriteria(start=date(2019, 6, 1), end=date(2019, 8, 31))

    page = await load_timeline_page(
        fake,
        criteria=criteria,
        edge="earliest",
        jump=None,
        cursor_token=None,
        limit=48,
    )

    assert [item.id for item in page.photos] == ["july"]
    assert fake.bodies[1]["cursor"] == "next"
    assert decode_cursor(page.newer_cursor)["immich"] == "after-july"
    assert page.older_cursor is None


def test_jump_offers_a_newer_page_and_an_older_page():
    asyncio.run(_jump())


async def _jump():
    hit = photo(id="july", taken_at=datetime(2019, 7, 4, 12, 0))
    fake = FakeImmich([_page(hit, cursor="newer-page")])
    criteria = TimelineCriteria(person_ids=(MOM,), start=date(2019, 6, 1), end=date(2019, 8, 31))

    page = await load_timeline_page(
        fake,
        criteria=criteria,
        edge="latest",
        jump=date(2019, 7, 1),
        cursor_token=None,
        limit=48,
    )

    newer = decode_cursor(page.newer_cursor)
    older = decode_cursor(page.older_cursor)
    assert newer["direction"] == "asc"
    assert newer["immich"] == "newer-page"
    assert newer["jump"] == "2019-07-01"
    assert older["direction"] == "desc"
    assert older["immich"] is None
    assert older["before"] == "2019-07-01"
    assert older["jump"] is None
    assert fake.bodies[0]["orderBy"]["direction"] == "asc"
    assert "cursor" not in fake.bodies[0]

    before = photo(id="june", taken_at=datetime(2019, 6, 30, 18, 0))
    too_early = photo(id="may", taken_at=datetime(2019, 5, 20, 18, 0))
    fake.pages.append(_page(before, too_early, cursor=None))
    older_page = await load_timeline_page(
        fake,
        criteria=TimelineCriteria(),
        edge="latest",
        jump=None,
        cursor_token=page.older_cursor,
        limit=48,
    )
    assert [item.id for item in older_page.photos] == ["june"]
    assert older_page.newer_cursor is None


def test_smart_search_is_one_chronological_page():
    asyncio.run(_smart())


async def _smart():
    later = photo(id="later", taken_at=datetime(2019, 8, 1, 12, 0))
    earlier = photo(id="earlier", taken_at=datetime(2019, 7, 4, 12, 0))
    fake = FakeImmich([_page(later, earlier, cursor="ignored")])
    criteria = TimelineCriteria(person_ids=(MOM,), text="beach", start=date(2019, 6, 1), end=date(2019, 8, 31))

    page = await load_timeline_page(
        fake,
        criteria=criteria,
        edge="latest",
        jump=None,
        cursor_token=None,
        limit=48,
    )

    assert [item.id for item in page.photos] == ["earlier", "later"]
    assert page.older_cursor is None
    assert page.newer_cursor is None
    assert fake.smart[0]["query"] == "beach"
    assert fake.smart[0]["filter"]["personIds"] == {"all": [MOM]}
    assert fake.bodies == []
