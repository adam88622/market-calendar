"""FN-040~043 pipelines（離線）。"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import pipelines
from core.config import load_config
from core.models import FetchResult, IrRecord, RunContext, UsEarningRecord, WatchItem
from storage.db import init_db

FX = Path(__file__).resolve().parent / "fixtures"
TODAY = date(2026, 11, 8)
NOW = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)


def make_ctx(tmp_path, tickers=None, **us_over):
    cfg = load_config(FX / "config_test.yaml", tmp_path)
    if tickers is not None or us_over:
        kw = dict(us_over)
        if tickers is not None:
            kw["watchlist"] = tuple(WatchItem(t, "G") for t in tickers)
        cfg = replace(cfg, us_earnings=replace(cfg.us_earnings, **kw))
    conn = init_db(tmp_path / "calendar.db")
    logger = logging.getLogger("market_calendar.test_pipelines")
    logger.setLevel(logging.DEBUG)
    return RunContext(
        cfg=cfg, prj_root=tmp_path, today=TODAY, now_utc=NOW,
        range_start=date(2026, 8, 1), range_end=date(2027, 11, 30),
        conn=conn, session=None, logger=logger,
    )


@pytest.fixture
def ctx(tmp_path):
    c = make_ctx(tmp_path)
    yield c
    c.conn.close()


def rec(d=date(2026, 12, 1)):
    return UsEarningRecord("X", d, "16:00", "盤後", "確認")


# ------------------------------------------------------------ FN-040 / 043
def test_tw_settlement(ctx):
    evs = pipelines.produce_tw_settlement(ctx)
    assert evs
    for e in evs:
        d = e.start.date() if isinstance(e.start, datetime) else e.start
        assert ctx.range_start <= d <= ctx.range_end


def test_us_settlement_every_month(ctx):
    evs = pipelines.produce_us_settlement(ctx)
    months = {(d.year, d.month) for d in
              (e.start for e in evs)}
    assert len(months) == 16
    vix = [e for e in evs if "VIX" in e.title]
    assert len(vix) == 16
    assert len([e for e in evs if "VIX" not in e.title]) == 16


# ------------------------------------------------------------ FN-041
def test_tw_earnings_mocked(ctx, monkeypatch):
    from scrapers.mops_ir import parse_mops_ir_html
    html = (FX / "mops_t100sb02_1_sii_115_11.html").read_text(encoding="utf-8")
    recs = parse_mops_ir_html(html, "sii")
    assert recs
    monkeypatch.setattr(pipelines, "fetch_mops_ir",
                        lambda s, c, t: FetchResult(records=recs))
    monkeypatch.setattr(pipelines, "fetch_yahoo_ir",
                        lambda s, c: FetchResult(records=[]))
    evs = pipelines.produce_tw_earnings(ctx)
    ir = [e for e in evs if "法說會" in e.title]
    assert len(ir) == len({(r.stock_id, r.event_date) for r in recs})
    assert any("截止" in e.title for e in evs)
    assert len({e.uid for e in evs}) == len(evs)


def test_tw_earnings_all_fail(ctx, monkeypatch, caplog):
    monkeypatch.setattr(pipelines, "fetch_mops_ir",
                        lambda s, c, t: FetchResult(records=[], errors=["boom"]))
    monkeypatch.setattr(pipelines, "fetch_yahoo_ir",
                        lambda s, c: FetchResult(records=[], errors=["boom"]))
    ctx.logger.propagate = True
    with caplog.at_level(logging.WARNING):
        evs = pipelines.produce_tw_earnings(ctx)
    assert evs  # 截止日仍產出
    assert any(r.levelno == logging.WARNING for r in caplog.records)


# ------------------------------------------------------------ FN-042
TICKERS = [f"T{i:02d}" for i in range(57)]
NOSLEEP = lambda s: None  # noqa: E731


def db_order(conn):
    return [r[0] for r in conn.execute("SELECT ticker FROM us_earnings ORDER BY rowid")]


def test_us_all_success_order(tmp_path):
    ctx = make_ctx(tmp_path, TICKERS, max_workers=6, per_ticker_timeout_sec=5.0,
                   total_budget_sec=20.0)
    import random

    def fetcher(t, limit, today_et):
        time.sleep(random.random() * 0.03)  # 打亂完成順序
        return [rec()]

    evs, hung, _deg = pipelines.produce_us_earnings(ctx, fetcher=fetcher, sleep=NOSLEEP)
    assert hung == 0
    assert db_order(ctx.conn) == TICKERS
    assert len(evs) == 57
    ctx.conn.close()


def test_us_failure_and_hang(tmp_path, caplog):
    ctx = make_ctx(tmp_path, TICKERS, max_workers=6, per_ticker_timeout_sec=0.4,
                   total_budget_sec=20.0)
    ctx.logger.propagate = True
    release = threading.Event()

    def fetcher(t, limit, today_et):
        if t == "T01":
            raise RuntimeError("AMD-like boom")
        if t == "T09":
            release.wait(30)
        return [rec()]

    try:
        t0 = time.monotonic()
        with caplog.at_level(logging.WARNING):
            evs, hung, _deg = pipelines.produce_us_earnings(ctx, fetcher=fetcher, sleep=NOSLEEP)
        elapsed = time.monotonic() - t0
    finally:
        release.set()
    assert hung == 1
    assert elapsed < 3
    assert len(evs) == 55
    got = set(db_order(ctx.conn))
    assert "T01" not in got and "T09" not in got and len(got) == 55
    text = caplog.text
    assert "T01 fetch failed" in text and "T09 timeout" in text
    ctx.conn.close()


def test_us_concurrency_cap(tmp_path):
    ctx = make_ctx(tmp_path, TICKERS[:30], max_workers=4, per_ticker_timeout_sec=5.0,
                   total_budget_sec=20.0)
    lock = threading.Lock()
    cur = peak = 0

    def fetcher(t, limit, today_et):
        nonlocal cur, peak
        with lock:
            cur += 1
            peak = max(peak, cur)
        time.sleep(0.02)
        with lock:
            cur -= 1
        return [rec()]

    _, hung, _deg = pipelines.produce_us_earnings(ctx, fetcher=fetcher, sleep=NOSLEEP)
    assert hung == 0
    assert 1 <= peak <= 4
    ctx.conn.close()


def test_us_tiny_budget_returns(tmp_path):
    ctx = make_ctx(tmp_path, TICKERS[:12], max_workers=2, per_ticker_timeout_sec=5.0,
                   total_budget_sec=0.1)
    release = threading.Event()

    def fetcher(t, limit, today_et):
        release.wait(30)
        return [rec()]

    try:
        t0 = time.monotonic()
        evs, hung, _deg = pipelines.produce_us_earnings(ctx, fetcher=fetcher, sleep=NOSLEEP)
        assert time.monotonic() - t0 < 3
    finally:
        release.set()
    assert evs == []
    assert hung == 2  # 兩條 worker 卡住，其餘排隊中被取消
    ctx.conn.close()


def test_us_warmup_failure_still_continues(tmp_path):
    # 預算 6 < per_timeout(2) + 5 → 不重試
    ctx = make_ctx(tmp_path, TICKERS[:5], max_workers=3, per_ticker_timeout_sec=2.0,
                   total_budget_sec=6.0)
    calls = []

    def fetcher(t, limit, today_et):
        calls.append(t)
        if t == "T00":
            raise RuntimeError("crumb 401")
        return [rec()]

    evs, hung, _deg = pipelines.produce_us_earnings(ctx, fetcher=fetcher, sleep=NOSLEEP)
    assert calls.count("T00") == 1  # 預算不足 → 不重送
    assert len(evs) == 4 and hung == 0
    ctx.conn.close()


def test_us_warmup_first_timeout_second_success(tmp_path):
    ctx = make_ctx(tmp_path, TICKERS[:5], max_workers=3, per_ticker_timeout_sec=0.3,
                   total_budget_sec=6.0)  # 6 >= 0.3 + 5 → 允許重試
    release = threading.Event()
    calls = []

    def fetcher(t, limit, today_et):
        calls.append(t)
        if t == "T00" and calls.count("T00") == 1:
            release.wait(30)  # 第一次卡住
        return [rec()]

    try:
        evs, hung, deg = pipelines.produce_us_earnings(ctx, fetcher=fetcher, sleep=NOSLEEP)
    finally:
        release.set()
    assert calls.count("T00") == 2
    assert "T00" in db_order(ctx.conn)  # 重試結果被採用
    assert len(evs) == 5 and deg is False
    assert hung == 1  # 逾時的第一次 future 仍計入 hung
    ctx.conn.close()


def test_us_warmup_no_retry_when_budget_short(tmp_path):
    ctx = make_ctx(tmp_path, TICKERS[:3], max_workers=2, per_ticker_timeout_sec=0.3,
                   total_budget_sec=3.0)  # 3 < 0.3 + 5
    release = threading.Event()
    calls = []

    def fetcher(t, limit, today_et):
        calls.append(t)
        if t == "T00":
            release.wait(30)
        return [rec()]

    try:
        pipelines.produce_us_earnings(ctx, fetcher=fetcher, sleep=NOSLEEP)
    finally:
        release.set()
    assert calls.count("T00") == 1
    ctx.conn.close()


def _degraded_run(tmp_path, n_fail, caplog):
    ctx = make_ctx(tmp_path, TICKERS[:10], max_workers=4, per_ticker_timeout_sec=2.0,
                   total_budget_sec=6.0)
    ctx.logger.propagate = True
    bad = set(TICKERS[1:1 + n_fail])

    def fetcher(t, limit, today_et):
        if t in bad:
            raise RuntimeError("boom")
        return [rec()]

    with caplog.at_level(logging.WARNING):
        out = pipelines.produce_us_earnings(ctx, fetcher=fetcher, sleep=NOSLEEP)
    ctx.conn.close()
    return out


def test_us_degraded_at_70_percent(tmp_path, caplog):
    evs, hung, deg = _degraded_run(tmp_path, 3, caplog)
    assert deg is True and len(evs) == 7
    assert "us-earnings degraded: 7/10 fetched; missing tickers keep cached data" in caplog.text


def test_us_not_degraded_at_90_percent(tmp_path, caplog):
    evs, hung, deg = _degraded_run(tmp_path, 1, caplog)
    assert deg is False
    assert "us-earnings degraded" not in caplog.text


# ------------------------------------------------------------ FN-065
def _fake_closures(spec, start, end):
    from core.models import EarlyClose, MarketClosures

    if spec.code == "us":
        early = (EarlyClose(date(2026, 11, 27),
                            datetime(2026, 11, 27, 13, 0, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))),)
        return MarketClosures("us", (date(2026, 11, 26), date(2026, 12, 25)), early)
    if spec.code == "jp":
        return MarketClosures("jp", (date(2026, 11, 3),), ())
    return MarketClosures("kr", (date(2026, 10, 9),), ())


def _fake_names(warns=()):
    from core.models import HolidayName

    def fn(spec, days):
        return {d: HolidayName("節日", "Holiday", "holidays") for d in days}, list(warns)

    return fn


def test_global_holidays_normal_fake(ctx):
    evs, hung, degraded = pipelines.produce_global_holidays(
        ctx, closures_fn=_fake_closures, names_fn=_fake_names())
    assert hung == 0 and degraded is False
    assert len(evs) == 2 + 1 + 1 + 1


def test_global_holidays_closures_exception_propagates(ctx):
    def boom(spec, s, e):
        if spec.code == "kr":
            raise RuntimeError("kr down")
        return _fake_closures(spec, s, e)

    with pytest.raises(RuntimeError, match="kr down"):
        pipelines.produce_global_holidays(ctx, closures_fn=boom, names_fn=_fake_names())


def test_global_holidays_warnings_degraded(ctx, caplog):
    with caplog.at_level(logging.WARNING, logger="market_calendar"):
        evs, _h, degraded = pipelines.produce_global_holidays(
            ctx, closures_fn=_fake_closures, names_fn=_fake_names(["pkg missing"]))
    assert degraded is True and evs
    assert any(r.levelno == logging.WARNING and "pkg missing" in r.getMessage()
               for r in caplog.records)


def test_global_holidays_real_packages(ctx):
    evs, hung, _degraded = pipelines.produce_global_holidays(ctx)
    assert hung == 0
    assert len(evs) == 58
    uids = {e.uid for e in evs}
    for u in ["gh-us-closed-20261126", "gh-us-early-20261127", "gh-us-closed-20261225",
              "gh-jp-closed-20260921", "gh-jp-closed-20260922", "gh-jp-closed-20260923",
              "gh-jp-closed-20261103", "gh-kr-closed-20260924", "gh-kr-closed-20260925",
              "gh-kr-closed-20261009"]:
        assert f"{u}@market-calendar-ics" in uids
