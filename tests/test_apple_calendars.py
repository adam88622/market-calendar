import logging
from datetime import date, datetime, timezone

import pytest

from apple.calendars import choose_source, ensure_calendar
from apple.models import (
    CAL_TYPE_CALDAV,
    SOURCE_CALDAV,
    SOURCE_LOCAL,
    URL_SCHEME,
    AppleCalendarError,
    EventPayload,
    RemoteEvent,
    SourceInfo,
)
from storage.apple_map_store import get_calendar_map, upsert_calendar_map
from storage.db import init_db
from tests.fake_calendar_adapter import FakeCalendarAdapter

NOW = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)
WIN = (date(2026, 1, 1), date(2027, 12, 31))
LOG = logging.getLogger("t")
ICLOUD = SourceInfo("s-icloud", "iCloud", SOURCE_CALDAV)
LOCAL = SourceInfo("s-local", "On My Mac", SOURCE_LOCAL)
OURS = URL_SCHEME + "uid-1"


@pytest.fixture
def conn(tmp_path):
    c = init_db(tmp_path / "t.db")
    yield c
    c.close()


def ens(fake, conn, title="台股財報", prefer="iCloud", exclude=frozenset()):
    return ensure_calendar(fake, conn, "tw-earnings", title, prefer, WIN, NOW, LOG, exclude)


def test_choose_icloud():
    s, fb = choose_source([LOCAL, ICLOUD], "iCloud")
    assert (s, fb) == (ICLOUD, False)


def test_choose_fallback_local():
    assert choose_source([LOCAL], "iCloud") == (LOCAL, True)


def test_choose_prefer_local():
    assert choose_source([LOCAL, ICLOUD], "local") == (LOCAL, False)
    assert choose_source([ICLOUD], "local") == (ICLOUD, True)


def test_choose_none():
    with pytest.raises(AppleCalendarError):
        choose_source([SourceInfo("x", "Exchange", 1)], "iCloud")


def test_choose_two_icloud_smallest():
    a = SourceInfo("b-src", "iCloud", SOURCE_CALDAV)
    b = SourceInfo("a-src", "iCloud", SOURCE_CALDAV)
    assert choose_source([a, b], "iCloud")[0] is b


def test_map_hit_no_create(conn):
    f = FakeCalendarAdapter()
    c = f.add_calendar("台股財報", "fake-src-icloud")
    upsert_calendar_map(conn, "tw-earnings", c, NOW)
    assert ens(f, conn).identifier == c.identifier
    assert "create_calendar" not in f.op_names()
    assert "rename_calendar" not in f.op_names()


def test_map_hit_renamed(conn):
    f = FakeCalendarAdapter()
    c = f.add_calendar("舊名", "fake-src-icloud")
    upsert_calendar_map(conn, "tw-earnings", c, NOW)
    info = ens(f, conn)
    assert "rename_calendar" in f.op_names()
    assert info.title == "台股財報"
    assert get_calendar_map(conn, "tw-earnings").title == "台股財報"


def test_map_id_deleted_rebuild(conn, caplog):
    f = FakeCalendarAdapter()
    c = f.add_calendar("台股財報", "fake-src-icloud")
    upsert_calendar_map(conn, "tw-earnings", c, NOW)
    f.user_delete_calendar(c.identifier)
    with caplog.at_level(logging.WARNING, logger="t"):
        info = ens(f, conn)
    assert info.identifier != c.identifier
    assert get_calendar_map(conn, "tw-earnings").calendar_identifier == info.identifier
    assert any("deleted by user" in r.message and "rebuilding" in r.message for r in caplog.records)


def test_map_not_writable_rebuild(conn, caplog):
    f = FakeCalendarAdapter()
    c = f.add_calendar("台股財報", "fake-src-icloud", writable=False)
    upsert_calendar_map(conn, "tw-earnings", c, NOW)
    with caplog.at_level(logging.WARNING, logger="t"):
        info = ens(f, conn)
    assert info.identifier != c.identifier
    assert any("rebuilding" in r.message for r in caplog.records)


def test_adopt_same_name_with_our_events(conn):
    f = FakeCalendarAdapter()
    c = f.add_calendar("台股財報", "fake-src-icloud")
    f.add_foreign_event(c.identifier, url=OURS)
    info = ens(f, conn)
    assert info.identifier == c.identifier
    assert "create_calendar" not in f.op_names()
    assert get_calendar_map(conn, "tw-earnings").calendar_identifier == c.identifier
    assert "mark_managed" not in f.op_names()


def test_adopt_multiple_picks_smallest(conn, caplog):
    f = FakeCalendarAdapter()
    b = f.add_calendar("台股財報", "fake-src-icloud", identifier="b")
    a = f.add_calendar("台股財報", "fake-src-icloud", identifier="a")
    for c in (a, b):
        f.add_foreign_event(c.identifier, url=OURS)
    with caplog.at_level(logging.WARNING, logger="t"):
        assert ens(f, conn).identifier == "a"
    assert any("adopting" in r.message for r in caplog.records)


def test_no_adopt_without_our_events_creates_new(conn, caplog):
    f = FakeCalendarAdapter()
    old = f.add_calendar("台股財報", "fake-src-icloud")
    f.add_foreign_event(old.identifier)  # uid=None
    before = f.events_of(old.identifier)
    with caplog.at_level(logging.WARNING, logger="t"):
        info = ens(f, conn)
    assert info.identifier != old.identifier
    assert any("contains no market-calendar-ics events" in r.message for r in caplog.records)
    assert f.events_of(old.identifier) == before
    assert f.calendars[old.identifier] == old


def test_unadopted_candidate_not_managed_and_immutable(conn):
    f = FakeCalendarAdapter()
    old = f.add_calendar("台股財報", "fake-src-icloud")
    item = f.add_foreign_event(old.identifier, title="mine")
    before = f.events_of(old.identifier)
    ens(f, conn)
    assert old.identifier not in f.managed
    remote = RemoteEvent(item, None, old.identifier, None, date(2026, 9, 1))
    with pytest.raises(AppleCalendarError):
        f.delete_event(remote)
    payload = EventPayload(
        "u", "tw-earnings", "x", True, date(2026, 9, 1), date(2026, 9, 1), "", "",
        URL_SCHEME + "u", 0, "h", date(2026, 9, 1),
    )
    with pytest.raises(AppleCalendarError):
        f.update_event(remote, payload)
    f.commit()
    assert f.events_of(old.identifier) == before


def test_subscription_not_adopted(conn):
    f = FakeCalendarAdapter()
    sub = f.add_calendar("台股財報", "fake-src-icloud", cal_type=3)
    f.add_foreign_event(sub.identifier, url=OURS)
    info = ens(f, conn)
    assert info.identifier != sub.identifier


def test_exclude_ids_not_adopted(conn):
    f = FakeCalendarAdapter()
    c = f.add_calendar("台股財報", "fake-src-icloud")
    f.add_foreign_event(c.identifier, url=OURS)
    info = ens(f, conn, exclude=frozenset({c.identifier}))
    assert info.identifier != c.identifier


def test_fallback_warning_and_local(conn, caplog):
    f = FakeCalendarAdapter(sources=[LOCAL])
    with caplog.at_level(logging.WARNING, logger="t"):
        info = ens(f, conn)
    assert info.source_type == SOURCE_LOCAL
    assert any("iCloud source not found" in r.message for r in caplog.records)


def test_create_in_icloud_and_map(conn):
    f = FakeCalendarAdapter()
    info = ens(f, conn)
    assert info.source_type == SOURCE_CALDAV and info.cal_type == CAL_TYPE_CALDAV
    assert get_calendar_map(conn, "tw-earnings").calendar_identifier == info.identifier
