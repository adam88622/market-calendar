"""FN-023 fetch_mops_ir、FN-024 fetch_yahoo_ir、FN-025 fetch_us_earnings（mock 網路）。"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import scrapers.mops_ir as mops_mod
import scrapers.yahoo_ir as yahoo_mod
import scrapers.yf_earnings as yf_mod
from core.models import FetchError
from scrapers.mops_ir import fetch_mops_ir
from scrapers.yahoo_ir import fetch_yahoo_ir
from scrapers.yf_earnings import fetch_us_earnings

FX = Path(__file__).resolve().parent / "fixtures"


def _read(n):
    return (FX / n).read_text(encoding="utf-8")


def make_cfg(**over):
    mops = dict(
        url="https://mops.example/ajax_t100sb02_1",
        referer="https://mops.example/t100sb02_1",
        markets=("sii", "otc"),
        request_interval_sec=2.5,
        timeout_sec=15,
        max_retries=2,
        backoff_sec=3,
        total_budget_sec=35,
    )
    mops.update(over)
    return SimpleNamespace(
        user_agent="UA-test",
        mops=SimpleNamespace(**mops),
        yahoo=SimpleNamespace(enabled=True, url="https://yahoo.example/ec", timeout_sec=10, max_retries=1),
    )


class Recorder:
    """假 request_with_retry：依 (TYPEK, year, month) 回 fixture 或丟錯。"""

    def __init__(self, table=None, fail=()):
        self.calls = []
        self.table = table or {}
        self.fail = set(fail)

    def __call__(self, session, method, url, **kw):
        d = kw["data"]
        key = (d["TYPEK"], d["year"], d["month"])
        self.calls.append((method, url, kw))
        if key in self.fail:
            raise FetchError("boom")
        return self.table.get(key, _read("mops_t100sb02_1_empty.html"))


SII11 = _read("mops_t100sb02_1_sii_115_11.html")
OTC11 = _read("mops_t100sb02_1_otc_115_11.html")
SII12 = _read("mops_t100sb02_1_sii_115_12.html")


# ---------------- FN-023 ----------------
def test_mops_all_scopes_ok(monkeypatch):
    rec = Recorder({("sii", "115", "11"): SII11, ("otc", "115", "11"): OTC11, ("sii", "115", "12"): SII12})
    monkeypatch.setattr(mops_mod, "request_with_retry", rec)
    sleeps = []
    res = fetch_mops_ir(object(), make_cfg(), date(2026, 11, 8), sleep=sleeps.append, clock=lambda: 0.0)

    assert len(rec.calls) == 4
    assert res.errors == []
    assert res.ok_scopes == [("上市", 2026, 11), ("上櫃", 2026, 11), ("上市", 2026, 12), ("上櫃", 2026, 12)]
    assert res.scope_counts == {
        ("上市", 2026, 11): 170,
        ("上櫃", 2026, 11): 69,
        ("上市", 2026, 12): 13,  # 15 筆中 2 筆起日在 11 月，不計入 12 月
        ("上櫃", 2026, 12): 0,   # empty fixture
    }
    assert sleeps == [2.5, 2.5, 2.5] and all(s >= 2 for s in sleeps)
    # form 與 headers
    method, url, kw = rec.calls[0]
    assert method == "POST" and url == "https://mops.example/ajax_t100sb02_1"
    assert kw["data"] == {
        "encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1,
        "TYPEK": "sii", "year": "115", "month": "11", "co_id": "",
    }
    assert kw["headers"]["User-Agent"] == "UA-test"
    assert kw["headers"]["Referer"] == "https://mops.example/t100sb02_1"
    assert kw["deadline"] == 35.0
    # records 以 (stock_id, date) 去重，跨 scope 的 11 月起日列只留一筆
    keys = [(r.stock_id, r.event_date) for r in res.records]
    assert len(keys) == len(set(keys)) == 252  # 170+69+15，扣掉 12 月查詢重複的 2 筆 11 月起日列
    assert res.records == sorted(res.records, key=lambda r: (r.event_date, r.stock_id))
    assert {r.market for r in res.records} == {"上市", "上櫃"}


def test_mops_one_scope_fails(monkeypatch):
    rec = Recorder({("sii", "115", "11"): SII11, ("otc", "115", "11"): OTC11},
                   fail={("sii", "115", "12")})
    monkeypatch.setattr(mops_mod, "request_with_retry", rec)
    res = fetch_mops_ir(object(), make_cfg(), date(2026, 11, 8), sleep=lambda s: None, clock=lambda: 0.0)
    assert len(res.ok_scopes) == 3
    assert ("上市", 2026, 12) not in res.ok_scopes
    assert ("上市", 2026, 12) not in res.scope_counts
    assert len(res.errors) == 1 and "sii 2026-12" in res.errors[0]
    assert len(res.records) == 170 + 69


def test_mops_parse_error_recorded(monkeypatch):
    rec = Recorder({("sii", "115", "11"): _read("mops_error_page.html")})
    monkeypatch.setattr(mops_mod, "request_with_retry", rec)
    res = fetch_mops_ir(object(), make_cfg(), date(2026, 11, 8), sleep=lambda s: None, clock=lambda: 0.0)
    assert len(res.errors) == 1 and "ParseError" in res.errors[0]
    assert ("上市", 2026, 11) not in res.ok_scopes
    assert len(res.ok_scopes) == 3


def test_mops_year_rollover(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(mops_mod, "request_with_retry", rec)
    res = fetch_mops_ir(object(), make_cfg(), date(2026, 12, 5), sleep=lambda s: None, clock=lambda: 0.0)
    got = [(c[2]["data"]["TYPEK"], c[2]["data"]["year"], c[2]["data"]["month"]) for c in rec.calls]
    assert got == [("sii", "115", "12"), ("otc", "115", "12"), ("sii", "116", "01"), ("otc", "116", "01")]
    assert res.ok_scopes == [("上市", 2026, 12), ("上櫃", 2026, 12), ("上市", 2027, 1), ("上櫃", 2027, 1)]
    assert res.scope_counts[("上市", 2027, 1)] == 0


def test_mops_budget_exhausted_skips_remaining(monkeypatch):
    rec = Recorder({("sii", "115", "11"): SII11, ("otc", "115", "11"): OTC11})
    monkeypatch.setattr(mops_mod, "request_with_retry", rec)
    t = {"now": 0.0}

    def clock():
        return t["now"]

    def sleep(s):
        t["now"] += s

    # 讓每次請求「耗」15 秒：第 1 個後 now=15，剩 20 > 2.5 → sleep → 第 2 個後 now≈32.5，剩 2.5 <= 2.5 → 其餘跳過
    orig = rec.__call__

    def slow(*a, **k):
        r = orig(*a, **k)
        t["now"] += 15
        return r

    monkeypatch.setattr(mops_mod, "request_with_retry", slow)
    res = fetch_mops_ir(object(), make_cfg(), date(2026, 11, 8), sleep=sleep, clock=clock)
    assert len(rec.calls) == 2
    assert res.ok_scopes == [("上市", 2026, 11), ("上櫃", 2026, 11)]
    assert len(res.errors) == 2
    assert all("timeout" in e and "budget" in e for e in res.errors)
    assert ("上市", 2026, 12) not in res.scope_counts


def test_mops_budget_zero_skips_all(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(mops_mod, "request_with_retry", rec)
    res = fetch_mops_ir(object(), make_cfg(total_budget_sec=0), date(2026, 11, 8),
                        sleep=lambda s: None, clock=lambda: 0.0)
    assert rec.calls == [] and res.ok_scopes == [] and len(res.errors) == 4


def test_mops_real_request_budget_exhausted_via_http_layer(monkeypatch):
    """不 mock request_with_retry：clock 越過 deadline 後 session 不再被呼叫。"""
    from unittest.mock import MagicMock

    session = MagicMock()
    resp = MagicMock(status_code=200, text=SII11)
    session.request.return_value = resp
    t = {"now": 0.0}

    def clock():
        return t["now"]

    def sleep(s):
        t["now"] += 40  # sleep 後直接超過預算

    res = fetch_mops_ir(session, make_cfg(), date(2026, 11, 8), sleep=sleep, clock=clock)
    assert session.request.call_count == 1
    assert res.ok_scopes == [("上市", 2026, 11)]
    assert len(res.errors) == 3


def test_mops_never_raises_on_unexpected_fetcherror(monkeypatch):
    def always(*a, **k):
        raise FetchError("down")

    monkeypatch.setattr(mops_mod, "request_with_retry", always)
    res = fetch_mops_ir(object(), make_cfg(), date(2026, 11, 8), sleep=lambda s: None, clock=lambda: 0.0)
    assert res.records == [] and res.ok_scopes == [] and len(res.errors) == 4


# ---------------- FN-024 ----------------
def test_yahoo_ok(monkeypatch):
    seen = {}

    def fake(session, method, url, **kw):
        seen.update(method=method, url=url, **kw)
        return _read("yahoo_earnings_call.html")

    monkeypatch.setattr(yahoo_mod, "request_with_retry", fake)
    res = fetch_yahoo_ir(object(), make_cfg(), clock=lambda: 100.0)
    assert len(res.records) == 179 and res.errors == []
    assert seen["method"] == "GET" and seen["url"] == "https://yahoo.example/ec"
    assert seen["timeout"] == 10 and seen["max_retries"] == 1
    assert seen["deadline"] == 115.0  # R1：clock()+15
    assert seen["headers"]["User-Agent"] == "UA-test"


def test_yahoo_fetch_error(monkeypatch):
    def fake(*a, **k):
        raise FetchError("x")

    monkeypatch.setattr(yahoo_mod, "request_with_retry", fake)
    res = fetch_yahoo_ir(object(), make_cfg())
    assert res.records == [] and len(res.errors) == 1


def test_yahoo_parse_error(monkeypatch):
    monkeypatch.setattr(yahoo_mod, "request_with_retry", lambda *a, **k: "<html></html>")
    res = fetch_yahoo_ir(object(), make_cfg())
    assert res.records == [] and len(res.errors) == 1 and "ParseError" in res.errors[0]


def test_yahoo_disabled(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("should not be called")

    monkeypatch.setattr(yahoo_mod, "request_with_retry", boom)
    cfg = make_cfg()
    cfg.yahoo.enabled = False
    res = fetch_yahoo_ir(object(), cfg)
    assert res.records == [] and res.errors == []


# ---------------- FN-025 ----------------
class FakeTicker:
    def __init__(self, df=None, cal=None, df_exc=None, cal_exc=None):
        self._df, self._cal, self._df_exc, self._cal_exc = df, cal, df_exc, cal_exc
        self.limit = None

    def get_earnings_dates(self, limit=12):
        self.limit = limit
        if self._df_exc:
            raise self._df_exc
        return self._df

    @property
    def calendar(self):
        if self._cal_exc:
            raise self._cal_exc
        return self._cal


def _df():
    import numpy as np
    from datetime import datetime
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    idx = pd.DatetimeIndex([datetime(2026, 11, 17, 16, 0, tzinfo=et), datetime(2026, 8, 26, 16, 0, tzinfo=et)],
                           name="Earnings Date")
    return pd.DataFrame({"Reported EPS": [np.nan, 2.22]}, index=idx)


TODAY = date(2026, 11, 8)


def test_us_both_ok(monkeypatch):
    ft = FakeTicker(_df(), {"Earnings Date": [date(2026, 11, 17)]})
    monkeypatch.setattr(yf_mod.yf, "Ticker", lambda t: ft)
    recs = fetch_us_earnings("NVDA", 12, TODAY)
    assert [(r.ticker, r.date_et, r.status) for r in recs] == [
        ("NVDA", date(2026, 8, 26), "已公布"),
        ("NVDA", date(2026, 11, 17), "確認"),
    ]
    assert ft.limit == 12


def test_us_only_df_ok(monkeypatch):
    ft = FakeTicker(_df(), cal_exc=RuntimeError("cal down"))
    monkeypatch.setattr(yf_mod.yf, "Ticker", lambda t: ft)
    recs = fetch_us_earnings("NVDA", 12, TODAY)
    assert len(recs) == 2 and recs[1].status == "未知"


def test_us_only_calendar_ok(monkeypatch):
    ft = FakeTicker(df_exc=RuntimeError("df down"), cal={"Earnings Date": [date(2026, 12, 1)]})
    monkeypatch.setattr(yf_mod.yf, "Ticker", lambda t: ft)
    recs = fetch_us_earnings("NVDA", 12, TODAY)
    assert [r.date_et for r in recs] == [date(2026, 12, 1)]


def test_us_both_fail_raises_fetcherror(monkeypatch):
    ft = FakeTicker(df_exc=RuntimeError("a"), cal_exc=RuntimeError("b"))
    monkeypatch.setattr(yf_mod.yf, "Ticker", lambda t: ft)
    with pytest.raises(FetchError, match="NVDA"):
        fetch_us_earnings("NVDA", 12, TODAY)


def test_us_ticker_construction_fails(monkeypatch):
    def bad(t):
        raise ValueError("x")

    monkeypatch.setattr(yf_mod.yf, "Ticker", bad)
    with pytest.raises(FetchError):
        fetch_us_earnings("NVDA", 12, TODAY)


def test_us_no_data_but_no_error_returns_empty(monkeypatch):
    ft = FakeTicker(None, {})
    monkeypatch.setattr(yf_mod.yf, "Ticker", lambda t: ft)
    assert fetch_us_earnings("ZZZ", 12, TODAY) == []
