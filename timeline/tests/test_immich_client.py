import asyncio
from datetime import datetime

import httpx
import pytest

from app.config import Settings
from app.immich import ImmichClient, ImmichError, display_ratio, parse_asset, rejects_image_target
from app.models import TimelineCriteria
from tests.helpers import ASSET, MOM

IMMICH = "http://immich.test:2283"


def settings() -> Settings:
    return Settings(immich_url=IMMICH, immich_api_key="test-key", immich_public_url="http://photos.example:2283")


def run(coro):
    return asyncio.run(coro)


def test_parse_asset_keeps_the_camera_clock_and_live_photo_flag():
    photo = parse_asset(
        {
            "id": ASSET,
            "type": "IMAGE",
            "localDateTime": "2019-07-14T15:30:45.804Z",
            "fileCreatedAt": "2019-07-14T22:30:45.000Z",
            "originalFileName": "IMG_2048.HEIC",
            "livePhotoVideoId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "exifInfo": {"city": "Santa Cruz", "state": "California", "country": "United States"},
            "people": [{"id": MOM, "name": "Mom", "thumbnailPath": "/internal"}],
        }
    )
    assert photo is not None
    assert photo.taken_at == datetime(2019, 7, 14, 15, 30, 45)
    assert photo.live is True
    assert photo.filename == "IMG_2048.HEIC"
    assert photo.city == "Santa Cruz"
    assert photo.people[0].name == "Mom"


def test_display_ratio_turns_sideways_photos_upright():
    landscape = {"exifImageWidth": 4032, "exifImageHeight": 3024, "orientation": "1"}
    portrait = {"exifImageWidth": 4032, "exifImageHeight": 3024, "orientation": "6"}
    assert display_ratio({}, landscape) == pytest.approx(4 / 3, rel=1e-3)
    assert display_ratio({}, portrait) == pytest.approx(3 / 4, rel=1e-3)
    assert display_ratio({"width": 1080, "height": 1920}, {}) == pytest.approx(0.5625)
    assert display_ratio({}, {}) is None
    assert display_ratio({}, {"exifImageWidth": 0, "exifImageHeight": 100}) is None
    assert display_ratio({}, {"exifImageWidth": 9000, "exifImageHeight": 100}) == 4.0


def test_parse_asset_records_the_display_shape():
    photo = parse_asset(
        {
            "id": ASSET,
            "type": "IMAGE",
            "localDateTime": "2019-07-14T15:30:45.804Z",
            "exifInfo": {"exifImageWidth": 4000, "exifImageHeight": 3000, "orientation": "8"},
        }
    )
    assert photo is not None
    assert photo.ratio == pytest.approx(0.75)


def test_parse_asset_skips_photos_hidden_under_a_stack():
    base = {"type": "IMAGE", "localDateTime": "2019-07-14T15:30:45.804Z"}
    top = parse_asset({**base, "id": ASSET, "stack": {"primaryAssetId": ASSET, "assetCount": 2}})
    hidden = parse_asset({**base, "id": MOM, "stack": {"primaryAssetId": ASSET, "assetCount": 2}})
    assert top is not None and top.moment == "2019-07-14T15:30:45.804Z"
    assert hidden is None


def test_rejects_original_and_fullsize_targets():
    assert rejects_image_target(f"{IMMICH}/api/assets/{ASSET}/original")
    assert rejects_image_target(f"{IMMICH}/api/assets/{ASSET}/thumbnail?size=original")
    assert rejects_image_target(f"{IMMICH}/api/assets/{ASSET}/thumbnail?size=fullsize")
    assert not rejects_image_target(f"{IMMICH}/api/assets/{ASSET}/thumbnail?size=preview")
    assert not rejects_image_target(f"{IMMICH}/api/assets/{ASSET}/thumbnail?size=thumbnail")


def test_metadata_search_sends_the_key_in_a_header_and_parses_the_page():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-api-key")
        seen["body"] = request.read()
        return httpx.Response(
            200,
            json={
                "assets": {
                    "items": [
                        {
                            "id": ASSET,
                            "type": "IMAGE",
                            "localDateTime": "2019-07-04T15:30:00.000Z",
                            "originalFileName": "IMG.HEIC",
                            "exifInfo": {"city": "Santa Cruz"},
                            "people": [],
                        }
                    ],
                    "nextCursor": "cursor-1",
                }
            },
        )

    async def scenario():
        client = ImmichClient(settings(), transport=httpx.MockTransport(handler))
        await client.open()
        try:
            return await client.search_metadata({"size": 1, "filter": {}})
        finally:
            await client.close()

    page = run(scenario())
    assert seen["key"] == "test-key"
    assert "test-key" not in seen["url"]
    assert b'"size": 1' in seen["body"] or b'"size":1' in seen["body"]
    assert page.next_cursor == "cursor-1"
    assert page.photos[0].city == "Santa Cruz"


def test_thumbnail_redirect_to_the_original_is_not_followed():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.params.get("size") == "preview":
            return httpx.Response(302, headers={"location": f"/api/assets/{ASSET}/original"})
        return httpx.Response(200, content=b"original-bytes", headers={"content-type": "image/jpeg"})

    async def scenario():
        client = ImmichClient(settings(), transport=httpx.MockTransport(handler))
        await client.open()
        try:
            await client.fetch_image("asset", ASSET, "preview")
        finally:
            await client.close()

    with pytest.raises(ImmichError, match="original"):
        run(scenario())
    assert len(calls) == 1
    assert calls[0].endswith("size=preview")


def test_redirect_to_another_host_is_not_followed():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://evil.example/api/assets/x/thumbnail?size=preview"})

    async def scenario():
        client = ImmichClient(settings(), transport=httpx.MockTransport(handler))
        await client.open()
        try:
            await client.fetch_image("asset", ASSET, "thumbnail")
        finally:
            await client.close()

    with pytest.raises(ImmichError):
        run(scenario())
    assert len(calls) == 1


def test_original_size_never_makes_a_request():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("original size must not be requested")

    async def scenario():
        client = ImmichClient(settings(), transport=httpx.MockTransport(handler))
        await client.open()
        try:
            await client.fetch_image("asset", ASSET, "original")
        finally:
            await client.close()

    with pytest.raises(ImmichError, match="thumbnail or preview"):
        run(scenario())


def test_people_places_and_bounds_use_the_documented_endpoints():
    routes = []

    def handler(request: httpx.Request) -> httpx.Response:
        routes.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path == "/api/people":
            page = int(request.url.params["page"])
            if page == 1:
                return httpx.Response(
                    200,
                    json={
                        "people": [{"id": MOM, "name": "Mom"}, {"id": "33333333-3333-4333-8333-333333333333", "name": ""}],
                        "hasNextPage": True,
                    },
                )
            return httpx.Response(
                200,
                json={"people": [{"id": "44444444-4444-4444-8444-444444444444", "name": "Dad"}], "hasNextPage": False},
            )
        if request.url.path == "/api/search/suggestions":
            if request.url.params["type"] == "city":
                return httpx.Response(200, json=["Santa Cruz", None, " ", "Austin"])
            return httpx.Response(200, json=[])
        if request.url.path == "/api/search/metadata":
            direction = request.read()
            taken = "2019-08-31T12:00:00.000Z" if b"desc" in direction else "2019-06-01T08:00:00.000Z"
            return httpx.Response(
                200,
                json={
                    "assets": {
                        "items": [{"id": ASSET, "type": "IMAGE", "localDateTime": taken, "people": []}],
                        "nextCursor": None,
                    }
                },
            )
        raise AssertionError(request.url.path)

    async def scenario():
        client = ImmichClient(settings(), transport=httpx.MockTransport(handler))
        await client.open()
        try:
            people = await client.list_people()
            places = await client.list_places()
            earliest, latest = await client.date_bounds(TimelineCriteria())
            return people, places, earliest, latest
        finally:
            await client.close()

    people, places, earliest, latest = run(scenario())
    assert [person.name for person in people] == ["Dad", "Mom", ""]
    assert places["cities"] == ["Austin", "Santa Cruz"]
    assert earliest.isoformat() == "2019-06-01"
    assert latest.isoformat() == "2019-08-31"
    assert ("GET", "/api/people", routes[0][2])[0] == "GET"
    assert any(path == "/api/search/suggestions" and query.get("type") == "country" for _, path, query in routes)
    assert routes[0][2]["withHidden"] == "false"


def test_rejected_api_key_does_not_echo_the_key():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    async def scenario():
        client = ImmichClient(settings(), transport=httpx.MockTransport(handler))
        await client.open()
        try:
            await client.search_metadata({})
        finally:
            await client.close()

    with pytest.raises(ImmichError, match="API key") as caught:
        run(scenario())
    assert "test-key" not in str(caught.value)
