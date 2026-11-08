"""FN-062 測試（真實 pandas_market_calendars，離線計算）。"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from core.market_closures import compute_market_closures
from core.models import MARKET_SPECS

SPEC = {s.code: s for s in MARKET_SPECS}
START, END = date(2026, 8, 1), date(2027, 11, 30)


@pytest.fixture(scope="module")
def res():
    return {c: compute_market_closures(SPEC[c], START, END) for c in SPEC}


def test_us(res):
    us = res["us"]
    assert date(2026, 11, 26) in us.closed and date(2026, 12, 25) in us.closed
    e = {x.day: x for x in us.early}
    ec = e[date(2026, 11, 27)].close_local
    assert (ec.hour, ec.minute) == (13, 0)
    assert str(ec.tzinfo) == "America/New_York"
    assert ec.utcoffset() == timedelta(hours=-5)


def test_jp(res):
    jp = res["jp"]
    for d in [(2026, 9, 21), (2026, 9, 22), (2026, 9, 23), (2026, 11, 3)]:
        assert date(*d) in jp.closed
    assert jp.early == ()


def test_kr(res):
    kr = res["kr"]
    for d in [(2026, 9, 24), (2026, 9, 25), (2026, 10, 9)]:
        assert date(*d) in kr.closed
    assert kr.early == ()


def test_weekdays_sorted(res):
    for c in res.values():
        assert all(d.weekday() < 5 for d in c.closed)
        assert list(c.closed) == sorted(c.closed)
        assert [e.day for e in c.early] == sorted(e.day for e in c.early)


def test_count_regression_pmc_5_6_0(res):
    # 綁定 pmc 5.6.0；升級後若變動需人工確認再更新
    assert len(res["us"].closed) == 12
    assert len(res["us"].early) == 3
    assert len(res["jp"].closed) == 24
    assert len(res["kr"].closed) == 19


class _FakeCal:
    def __init__(self, valid_fn):
        self._v = valid_fn

    def valid_days(self, start_date, end_date):
        import pandas as pd
        return self._v(pd.bdate_range(start_date, end_date))


def _fake_mod(valid_fn=None, boom=None):
    def get_calendar(name):
        if boom:
            raise boom
        return _FakeCal(valid_fn)
    return SimpleNamespace(get_calendar=get_calendar)


def test_all_weekdays_valid_raises():
    mod = _fake_mod(lambda idx: idx)
    with pytest.raises(RuntimeError, match="no closures"):
        compute_market_closures(SPEC["jp"], START, END, mcal_module=mod)


def test_get_calendar_error_propagates():
    mod = _fake_mod(boom=KeyError("nope"))
    with pytest.raises(KeyError):
        compute_market_closures(SPEC["jp"], START, END, mcal_module=mod)


def test_start_after_end():
    with pytest.raises(ValueError):
        compute_market_closures(SPEC["jp"], END, START)


def test_too_many_closures_raises():
    mod = _fake_mod(lambda idx: idx[: len(idx) // 2])
    with pytest.raises(RuntimeError, match="suspicious"):
        compute_market_closures(SPEC["jp"], START, END, mcal_module=mod)


def test_coverage_guard_missing_year():
    # 只有 2026 年有休市；2027 年平日數遠超 60 卻 0 休市 → RuntimeError
    def valid(idx):
        keep = [t for t in idx if not (t.year == 2026 and t.month == 12 and t.day == 25)]
        return idx[idx.isin(keep)]
    mod = _fake_mod(valid)
    with pytest.raises(RuntimeError, match="2027"):
        compute_market_closures(SPEC["jp"], START, END, mcal_module=mod)


def test_coverage_guard_skips_short_year():
    # 2027 年只有 10 個平日（< 60）→ 不檢查該年
    def valid(idx):
        return idx[idx != __import__("pandas").Timestamp("2026-12-25")]
    mod = _fake_mod(valid)
    r = compute_market_closures(SPEC["jp"], date(2026, 8, 1), date(2027, 1, 14), mcal_module=mod)
    assert r.closed == (date(2026, 12, 25),)
