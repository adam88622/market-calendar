from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from core.models import (
    ConfigError, Event, FetchError, FetchResult, ParseError, RunContext,
    SettlementDate, UID_SUFFIX, WatchItem,
)

TPE = ZoneInfo("Asia/Taipei")
UID = "tw-earnings-2330-20261113" + UID_SUFFIX


def mk(**kw):
    base = dict(uid=UID, calendar="tw-earnings", title="t", start=date(2026, 11, 13), all_day=True)
    base.update(kw)
    return Event(**base)


def test_all_day_event_ok():
    e = mk()
    assert e.end is None and e.description == ""


def test_timed_event_ok():
    e = mk(start=datetime(2026, 11, 13, 14, 0, tzinfo=TPE), all_day=False)
    assert e.start.tzinfo is not None


def test_all_day_with_datetime_rejected():
    with pytest.raises(ValueError):
        mk(start=datetime(2026, 11, 13, 0, 0, tzinfo=TPE))


def test_timed_naive_rejected():
    with pytest.raises(ValueError):
        mk(start=datetime(2026, 11, 13, 14, 0), all_day=False)


def test_timed_with_date_rejected():
    with pytest.raises(ValueError):
        mk(all_day=False)


@pytest.mark.parametrize("uid", ["abc-123", "has space-1" + UID_SUFFIX, "a.b-1" + UID_SUFFIX, UID_SUFFIX])
def test_bad_uid_rejected(uid):
    with pytest.raises(ValueError):
        mk(uid=uid)


def test_event_frozen():
    with pytest.raises(FrozenInstanceError):
        mk().title = "x"


def test_settlement_default_reason():
    assert SettlementDate(date(2026, 1, 1), date(2026, 1, 2)).reason == ""


def test_watch_item_frozen():
    w = WatchItem("NVDA", "AI")
    with pytest.raises(FrozenInstanceError):
        w.ticker = "X"


def test_fetch_result_defaults_independent():
    a, b = FetchResult(records=[]), FetchResult(records=[])
    a.ok_scopes.append(("上市", 2026, 11))
    a.scope_counts[("上市", 2026, 11)] = 3
    assert b.ok_scopes == [] and b.scope_counts == {} and b.errors == []


def test_run_context_hung_workers_default():
    ctx = RunContext(cfg=None, prj_root=None, today=date(2026, 11, 8),
                     now_utc=datetime(2026, 11, 8, tzinfo=timezone.utc),
                     range_start=date(2026, 8, 1), range_end=date(2027, 11, 30),
                     conn=None, session=None, logger=None)
    assert ctx.hung_workers == 0
    ctx.hung_workers += 2
    assert ctx.hung_workers == 2


def test_exceptions():
    for exc in (ConfigError, FetchError, ParseError):
        assert issubclass(exc, Exception)


# ---- FN-060 ----
def test_cal_order_v5():
    from core.models import CAL_ORDER
    assert len(CAL_ORDER) == 5
    assert CAL_ORDER[:4] == ("tw-settlement", "us-settlement", "tw-earnings", "us-earnings")
    assert CAL_ORDER[-1] == "global-holidays"


def test_market_specs():
    from core.models import MARKET_SPECS
    assert [m.code for m in MARKET_SPECS] == ["us", "jp", "kr"]
    assert [m.early_closes for m in MARKET_SPECS] == [True, False, False]
    assert [m.mcal_name for m in MARKET_SPECS] == ["XNYS", "JPX", "XKRX"]


def test_global_holidays_event_and_dataclasses():
    from core.models import EarlyClose, HolidayName, MarketClosures
    e = Event(uid="global-holidays-us-20261126" + UID_SUFFIX, calendar="global-holidays",
              title="美股休市", start=date(2026, 11, 26), all_day=True)
    assert e.calendar == "global-holidays"
    mc = MarketClosures("us", (date(2026, 11, 26),), ())
    assert mc.early == ()
    assert EarlyClose(date(2026, 11, 27), datetime(2026, 11, 27, 13, tzinfo=ZoneInfo("America/New_York")))
    assert HolidayName("休市", "", "none").source == "none"
