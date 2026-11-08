from datetime import date

import pytest

from apple.adapter import CalendarAdapter
from apple.models import URL_SCHEME, AppleCalendarError, EventPayload
from tests.fake_calendar_adapter import FakeCalendarAdapter


def payload(uid="u1", cal="C", d=date(2026, 9, 1), title="t", h="h"):
    return EventPayload(uid, "tw-earnings", title, True, d, d, "n", "", URL_SCHEME + uid, -3600, h, d)


@pytest.fixture
def fake():
    f = FakeCalendarAdapter()
    f.cal = f.add_calendar("Mine", "fake-src-icloud")
    f.mark_managed(f.cal.identifier)
    return f


def fetch(f, cal=None):
    return f.fetch_events((cal or f.cal).identifier, date(2026, 8, 1), date(2027, 11, 30))


def test_isinstance_protocol():
    assert isinstance(FakeCalendarAdapter(), CalendarAdapter)


def test_commit_applies_and_identifiers(fake):
    tok = fake.create_event(fake.cal.identifier, payload())
    assert fetch(fake) == []
    assert fake.identifiers(tok) == (None, tok)
    fake.commit()
    evt, item = fake.identifiers(tok)
    assert evt is not None and item == tok
    (r,) = fetch(fake)
    assert r.uid == "u1" and r.event_identifier == evt and r.calendar_item_identifier == tok


def test_rollback_discards(fake):
    fake.create_event(fake.cal.identifier, payload())
    fake.rollback()
    fake.commit()
    assert fetch(fake) == []
    assert fake.staged_count == 0


def test_update_delete_staged_until_commit(fake):
    fake.create_event(fake.cal.identifier, payload())
    fake.commit()
    (r,) = fetch(fake)
    fake.update_event(r, payload(title="new", h="h2"))
    assert fake.events_of(fake.cal.identifier)[0]["title"] == "t"
    fake.rollback()
    fake.update_event(r, payload(title="new", h="h2"))
    fake.commit()
    assert fake.events_of(fake.cal.identifier)[0]["title"] == "new"
    fake.delete_event(r)
    fake.rollback()
    assert len(fetch(fake)) == 1
    fake.delete_event(r)
    fake.commit()
    assert fetch(fake) == []


def test_commit_failure_keeps_state_then_rollback(fake):
    fake.create_event(fake.cal.identifier, payload())
    fake.fail_on = {"commit": {"*"}}
    with pytest.raises(AppleCalendarError):
        fake.commit()
    assert fetch(fake) == []
    fake.rollback()
    fake.fail_on = {}
    fake.commit()
    assert fetch(fake) == []


def test_unmanaged_calendar_rejected_and_unchanged(fake):
    other = fake.add_calendar("Mine", "fake-src-icloud")  # 同名候選，未接手
    fid = fake.add_foreign_event(other.identifier, url=URL_SCHEME + "x1")
    (r,) = fetch(fake, other)
    with pytest.raises(AppleCalendarError):
        fake.delete_event(r)
    with pytest.raises(AppleCalendarError):
        fake.update_event(r, payload("x1"))
    with pytest.raises(AppleCalendarError):
        fake.create_event(other.identifier, payload("x2"))
    fake.commit()
    assert [e["calendar_item_identifier"] for e in fake.events_of(other.identifier)] == [fid]
    assert fake.events_of(other.identifier)[0]["title"] == "foreign"
    # fetch 不自動登記
    assert other.identifier not in fake.managed


def test_mark_managed_unknown_calendar(fake):
    with pytest.raises(AppleCalendarError):
        fake.mark_managed("nope")


def test_window_over_4_years(fake):
    with pytest.raises(ValueError):
        fake.fetch_events(fake.cal.identifier, date(2020, 1, 1), date(2024, 1, 1))
    fake.fetch_events(fake.cal.identifier, date(2023, 1, 2), date(2026, 12, 31))


def test_fault_injection_message_has_uid(fake):
    fake.fail_on = {"create": {"u1"}}
    with pytest.raises(AppleCalendarError, match="u1"):
        fake.create_event(fake.cal.identifier, payload("u1"))
    fake.create_event(fake.cal.identifier, payload("u2"))
    fake.fail_on = {"fetch": {fake.cal.identifier}}
    with pytest.raises(AppleCalendarError):
        fetch(fake)


def test_user_delete_and_foreign_and_change_identifier(fake):
    t = fake.create_event(fake.cal.identifier, payload())
    fake.commit()
    fake.add_foreign_event(fake.cal.identifier, "mine")
    assert sorted(r.uid for r in fetch(fake) if r.uid) == ["u1"]
    assert len(fetch(fake)) == 2
    new = fake.change_item_identifier("u1")
    assert new != t
    (r,) = fake.find_by_uid("u1")
    assert r.calendar_item_identifier == new
    with pytest.raises(AppleCalendarError):
        fake.delete_event(type(r)(t, None, r.calendar_identifier, "u1", r.start_date))
    assert fake.user_delete_event("u1") == 1
    assert fake.find_by_uid("u1") == []


def test_user_delete_calendar(fake):
    fake.user_delete_calendar(fake.cal.identifier)
    assert fake.get_calendar(fake.cal.identifier) is None
    with pytest.raises(AppleCalendarError):
        fetch(fake)


def test_create_calendar_and_status(fake):
    info = fake.create_calendar("New", "fake-src-icloud")
    assert fake.get_calendar(info.identifier) == info and info in fake.list_calendars()
    assert info.identifier not in fake.managed
    with pytest.raises(AppleCalendarError):
        fake.create_calendar("X", "bad-src")
    fake.rename_calendar(info.identifier, "New2")
    assert fake.get_calendar(info.identifier).title == "New2"
    fake.create_event(fake.cal.identifier, payload())
    with pytest.raises(AppleCalendarError):
        fake.create_calendar("Y", "fake-src-icloud")
    fake.rollback()
    fake.set_status("denied")
    assert fake.authorization_status() == "denied"
    assert fake.request_full_access()[0] is False
    fake.set_status("notDetermined")
    assert fake.request_full_access() == (True, None)
    assert fake.authorization_status() == "fullAccess"


def test_ops_recorded(fake):
    fake.create_event(fake.cal.identifier, payload())
    fake.commit()
    assert fake.op_names() == ["mark_managed", "create", "commit"]
