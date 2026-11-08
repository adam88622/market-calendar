from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from apple.adapter import CalendarAdapter
from apple.models import (
    APPLE_MAP_VERSION,
    URL_SCHEME,
    AppleAuthorizationError,
    AppleCalendarError,
    CalendarInfo,
    EventPayload,
    PlannedCreate,
    RemoteEvent,
    SourceInfo,
    SyncPlan,
    SyncResult,
)


def _payload():
    return EventPayload(
        uid="u", cal_id="tw-settlement", title="t", all_day=True,
        start=date(2026, 11, 11), end=date(2026, 11, 11), notes="", location="",
        url=URL_SCHEME + "u", alarm_offset_sec=-14400, content_hash="h",
        start_date=date(2026, 11, 11),
    )


def test_constants():
    assert APPLE_MAP_VERSION == 1 and URL_SCHEME == "market-calendar-ics://"


def test_sync_result_ok():
    r = SyncResult(cal_id="a", calendar_identifier="x")
    assert r.ok
    r.errors.append("boom")
    assert not r.ok


def test_sync_result_defaults_not_shared():
    a, b = SyncResult("a", "x"), SyncResult("b", "y")
    a.errors.append("e")
    assert b.errors == []
    p1, p2 = SyncPlan(), SyncPlan()
    p1.unchanged.append("u")
    assert p2.unchanged == [] and p2.guarded is False


@pytest.mark.parametrize("obj", [
    SourceInfo("i", "iCloud", 2),
    CalendarInfo("i", "t", "s", "iCloud", 2, 1, True),
    _payload(),
    RemoteEvent("c", None, "cal", "u", date(2026, 1, 1)),
])
def test_frozen(obj):
    first = next(iter(obj.__dataclass_fields__))
    with pytest.raises(FrozenInstanceError):
        setattr(obj, first, "changed")


def test_planned_create_frozen():
    pc = PlannedCreate(_payload(), False)
    with pytest.raises(FrozenInstanceError):
        pc.recreated = True


def test_error_hierarchy():
    assert issubclass(AppleAuthorizationError, AppleCalendarError)


def test_adapter_protocol_structural():
    class Dummy:
        pass

    assert not isinstance(Dummy(), CalendarAdapter)
    names = [
        "authorization_status", "request_full_access", "list_sources", "list_calendars",
        "get_calendar", "create_calendar", "rename_calendar", "mark_managed", "fetch_events",
        "create_event", "update_event", "delete_event", "commit", "rollback", "identifiers",
    ]
    Full = type("Full", (), {n: (lambda self, *a, **k: None) for n in names})
    assert isinstance(Full(), CalendarAdapter)
    assert all(hasattr(CalendarAdapter, n) for n in names)
