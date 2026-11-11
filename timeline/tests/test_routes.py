from datetime import date, datetime

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import RawPage
from tests.helpers import ASSET, MOM, photo


class FakeImmich:
    def __init__(self):
        self.fetched = []

    async def search_metadata(self, _body):
        return RawPage([photo()], None)

    async def search_smart(self, _body):
        return RawPage([photo()], None)

    async def date_bounds(self, _criteria):
        return (date(2019, 7, 4), date(2019, 7, 4))

    async def list_people(self):
        from app.models import PersonRef

        return [PersonRef(MOM, "Mom")]

    async def list_places(self):
        return {"cities": ["Santa Cruz"], "states": ["California"], "countries": ["United States"]}

    async def get_asset(self, asset_id):
        assert asset_id == ASSET
        return photo(taken_at=datetime(2019, 7, 4, 15, 30, 0))

    async def fetch_image(self, kind, entity_id, size=None):
        self.fetched.append((kind, entity_id, size))
        return "image/jpeg", b"\xff\xd8\xff\xd9"


def _settings(**overrides) -> Settings:
    base = dict(
        immich_url="http://immich.test:2283",
        immich_api_key="server-side-key",
        immich_public_url="http://photos.example:2283",
    )
    base.update(overrides)
    return Settings(**base)


def test_healthz_is_open_and_pages_require_basic_auth():
    app = create_app(
        _settings(basic_user="family", basic_password="secret"),
        FakeImmich(),
    )
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert client.get("/").status_code == 401
        assert "Basic" in client.get("/").headers["www-authenticate"]
        page = client.get("/", auth=("family", "secret"))
        assert page.status_code == 200
        assert "Family Photos" in page.text
        assert client.get("/", auth=("family", "wrong")).status_code == 401


def test_timeline_proxies_thumbnails_and_never_asks_for_originals():
    fake = FakeImmich()
    app = create_app(_settings(), fake)
    with TestClient(app) as client:
        payload = client.get("/api/timeline").json()
        photo_json = payload["groups"][0]["photos"][0]
        assert photo_json["thumbnail_url"] == f"/media/assets/{ASSET}?size=thumbnail"
        assert "ratio" in photo_json
        assert photo_json["immich_url"] == f"http://photos.example:2283/photos/{ASSET}"
        assert "server-side-key" not in client.get("/api/timeline").text
        assert payload["bounds"] == {"earliest": "2019-07-04", "latest": "2019-07-04"}

        refused = client.get(f"/media/assets/{ASSET}?size=original")
        assert refused.status_code == 400
        assert fake.fetched == []

        image = client.get(photo_json["preview_url"])
        assert image.status_code == 200
        assert image.headers["content-type"].startswith("image/jpeg")
        assert image.content == b"\xff\xd8\xff\xd9"
        assert fake.fetched == [("asset", ASSET, "preview")]

        detail = client.get(f"/api/assets/{ASSET}").json()
        assert detail["people"][0]["name"] == "Mom"
        assert detail["city"] == "Santa Cruz"
        assert "server-side-key" not in str(detail)

        filters = client.get("/api/filters").json()
        assert filters["people"][0]["thumbnail_url"] == f"/media/people/{MOM}"
        assert filters["cities"] == ["Santa Cruz"]


def test_missing_api_key_keeps_the_process_healthy():
    app = create_app(_settings(immich_api_key=""), FakeImmich())
    with TestClient(app) as client:
        assert client.get("/healthz").json()["immich"] == "unconfigured"
        denied = client.get("/api/timeline")
        assert denied.status_code == 503
        assert "IMMICH_API_KEY" in denied.json()["detail"]


def test_bad_cursor_is_rejected():
    app = create_app(_settings(), FakeImmich())
    with TestClient(app) as client:
        response = client.get("/api/timeline", params={"cursor": "%%%"})
        assert response.status_code == 400


def test_index_versions_static_assets_so_browsers_refetch_them():
    from app.main import _ASSET_VERSION

    app = create_app(_settings(), FakeImmich())
    with TestClient(app) as http:
        page = http.get("/")
    assert f"/static/timeline.js?v={_ASSET_VERSION}" in page.text
    assert f"/static/timeline.css?v={_ASSET_VERSION}" in page.text
