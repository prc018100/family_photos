"""Shared photo fixtures for the timeline tests."""

from datetime import datetime

from app.models import PersonRef, Photo

MOM = "11111111-1111-4111-8111-111111111111"
DAD = "22222222-2222-4222-8222-222222222222"
ASSET = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def photo(**overrides) -> Photo:
    base = dict(
        id=ASSET,
        taken_at=datetime(2019, 7, 4, 15, 30, 0),
        city="Santa Cruz",
        state="California",
        country="United States",
        people=(PersonRef(MOM, "Mom"),),
        filename="IMG_0001.HEIC",
        type="IMAGE",
        live=True,
    )
    base.update(overrides)
    return Photo(**base)
