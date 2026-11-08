"""FN-055 實機測試：只在本機來源建暫時行事曆，finally 一定清理。

執行：MCI_EVENTKIT_TESTS=1 pytest -m eventkit tests/test_eventkit_live.py
"""
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from apple.eventkit_adapter import EventKitAdapter
from apple.models import SOURCE_LOCAL, EventPayload

pytestmark = pytest.mark.eventkit

TZ = ZoneInfo("Asia/Taipei")


@pytest.fixture(scope="module")
def adapter():
    if os.environ.get("MCI_EVENTKIT_TESTS") != "1":
        pytest.skip("MCI_EVENTKIT_TESTS != 1")
    ad = EventKitAdapter.create()
    if ad.authorization_status() != "fullAccess":
        pytest.skip("EventKit not fullAccess")
    return ad


def _payload(uid, cal_id, all_day, start, end, start_date, location=""):
    return EventPayload(
        uid=uid, cal_id="test", title=f"mci {uid}", all_day=all_day, start=start, end=end,
        notes="n", location=location, url=f"market-calendar-ics://{uid}",
        alarm_offset_sec=-3600, content_hash="h", start_date=start_date,
    )


def test_roundtrip_in_temp_local_calendar(adapter):
    local = [s for s in adapter.list_sources() if s.source_type == SOURCE_LOCAL]
    if not local:
        pytest.skip("no local source")
    info = adapter.create_calendar(f"mci-test-{os.getpid()}", local[0].identifier)
    cal_id = info.identifier
    try:
        adapter.mark_managed(cal_id)
        d = date(2030, 3, 15)
        u1, u2 = "mci-live-allday@mci", "mci-live-timed@mci"
        adapter.create_event(cal_id, _payload(u1, cal_id, True, d, d, d))
        t_start = datetime(2030, 3, 15, 21, 30, tzinfo=TZ)
        t_end = datetime(2030, 3, 15, 22, 30, tzinfo=TZ)
        adapter.create_event(cal_id, _payload(u2, cal_id, False, t_start, t_end, d))
        adapter.commit()

        win = (date(2030, 3, 1), date(2030, 3, 31))
        remotes = {r.uid: r for r in adapter.fetch_events(cal_id, *win)}
        assert set(remotes) == {u1, u2}
        for r in remotes.values():
            assert r.start_date == d
            ev = adapter._handles[r.calendar_item_identifier]
            assert [a.relativeOffset() for a in ev.alarms()] == [-3600.0]
            assert ev.availability() == 1  # EKEventAvailabilityFree
        allday = adapter._handles[remotes[u1].calendar_item_identifier]
        assert allday.isAllDay()
        assert allday.endDate().timeIntervalSinceDate_(allday.startDate()) < 2 * 86400

        adapter.update_event(remotes[u1], _payload(u1, cal_id, True, d, d, d, location="X"))
        adapter.commit()
        again = {r.uid: r for r in adapter.fetch_events(cal_id, *win)}
        assert adapter._handles[again[u1].calendar_item_identifier].location() == "X"

        for r in again.values():
            adapter.delete_event(r)
        adapter.commit()
        assert adapter.fetch_events(cal_id, *win) == []
    finally:
        try:
            adapter.rollback()
            cal = adapter._store.calendarWithIdentifier_(cal_id)
            if cal is not None:
                adapter._store.removeCalendar_commit_error_(cal, True, None)
        except Exception:  # noqa: BLE001
            pass
