from datetime import date, datetime

from app.grouping import group_photos, photo_matches
from app.models import PersonRef, TimelineCriteria
from app.present import present_photo
from tests.helpers import DAD, MOM, photo


def test_groups_by_local_day_and_marks_year_changes():
    photos = [
        photo(id="b", taken_at=datetime(2020, 1, 1, 0, 5)),
        photo(id="a", taken_at=datetime(2019, 12, 31, 23, 50)),
        photo(id="a", taken_at=datetime(2019, 12, 31, 9, 0), filename="duplicate.jpg"),
        photo(id="c", taken_at=datetime(2019, 12, 31, 8, 0)),
    ]
    groups = group_photos(photos)

    assert [group.key for group in groups] == ["2019-12-31", "2020-01-01"]
    assert [item.id for item in groups[0].photos] == ["c", "a"]
    assert groups[0].show_year is True
    assert groups[0].show_month is True
    assert groups[1].show_year is True
    assert groups[1].label == "January 1, 2020"
    assert groups[1].weekday == "Wednesday"


def test_month_change_inside_a_year_does_not_repeat_the_year_mark():
    groups = group_photos(
        [
            photo(id="a", taken_at=datetime(2019, 7, 14, 9, 0)),
            photo(id="b", taken_at=datetime(2019, 8, 2, 9, 0)),
        ]
    )
    assert groups[0].show_year is True
    assert groups[1].show_year is False
    assert groups[1].show_month is True
    assert groups[1].month_label == "August 2019"


def test_mom_at_the_beach_in_summer_2019():
    """Person, place, and dates combine. Every selected person must be in the photo."""

    summer = TimelineCriteria(
        person_ids=(MOM,),
        city="Santa Cruz",
        state="California",
        country="United States",
        start=date(2019, 6, 1),
        end=date(2019, 8, 31),
    )
    mom_in_summer = photo()
    mom_in_winter = photo(taken_at=datetime(2019, 12, 1, 12, 0))
    dad_in_summer = photo(people=(PersonRef(DAD, "Dad"),))
    mom_in_ohio = photo(state="Ohio", city="Columbus")
    mom_on_june_first = photo(taken_at=datetime(2019, 6, 1, 0, 1))
    mom_on_september_first = photo(taken_at=datetime(2019, 9, 1, 0, 0))

    assert photo_matches(mom_in_summer, summer)
    assert photo_matches(mom_on_june_first, summer)
    assert not photo_matches(mom_in_winter, summer)
    assert not photo_matches(dad_in_summer, summer)
    assert not photo_matches(mom_in_ohio, summer)
    assert not photo_matches(mom_on_september_first, summer)

    both = TimelineCriteria(person_ids=(DAD, MOM))
    together = photo(people=(PersonRef(MOM, "Mom"), PersonRef(DAD, "Dad")))
    assert photo_matches(together, both)
    assert not photo_matches(mom_in_summer, both)


def test_jump_and_before_windows_do_not_overlap():
    criteria = TimelineCriteria(start=date(2019, 6, 1), end=date(2019, 8, 31))
    jump = date(2019, 7, 1)
    on_jump_day = photo(taken_at=datetime(2019, 7, 1, 8, 0))
    day_before = photo(taken_at=datetime(2019, 6, 30, 21, 0))

    assert photo_matches(on_jump_day, criteria, jump=jump)
    assert not photo_matches(day_before, criteria, jump=jump)
    assert photo_matches(day_before, criteria, before=jump)
    assert not photo_matches(on_jump_day, criteria, before=jump)


def test_empty_input_groups_to_nothing():
    assert group_photos([]) == []


def test_presented_photo_uses_proxied_renditions():
    body = present_photo(photo(), "http://photos.example:2283")
    assert body["thumbnail_url"] == f"/media/assets/{body['id']}?size=thumbnail"
    assert body["preview_url"].endswith("size=preview")
    assert body["immich_url"] == f"http://photos.example:2283/photos/{body['id']}"
    assert "api-key" not in str(body)
    assert "original" not in body["thumbnail_url"]
    assert body["taken_at"] == "2019-07-04T15:30:00"
