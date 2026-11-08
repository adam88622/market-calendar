"""FN-040~043：四個行事曆的事件產生流程。"""
from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import date
from typing import Callable
from zoneinfo import ZoneInfo

from core.holiday_names import lookup_holiday_names
from core.holidays import build_nyse_calendar, build_taifex_calendars
from core.market_closures import compute_market_closures
from core.models import MARKET_SPECS, Event, MarketClosures, RunContext, UsEarningRecord
from processors.global_holidays import build_global_holiday_events
from processors.tw_earnings import (
    build_tw_filing_events,
    build_tw_ir_events,
    merge_ir_records,
)
from processors.tw_settlement import build_tw_settlement_events
from processors.us_earnings import build_us_earnings_events
from processors.us_settlement import build_us_settlement_events
from scrapers.mops_ir import fetch_mops_ir
from scrapers.yahoo_ir import fetch_yahoo_ir
from scrapers.yf_earnings import fetch_us_earnings
from storage.ir_store import load_ir_records, upsert_ir_records
from storage.us_store import load_us_earnings, upsert_us_earnings

_ET = ZoneInfo("America/New_York")
_POLL_SEC = 0.05
_WARMUP_RETRY_MARGIN_SEC = 5.0
DEGRADED_RATIO = 0.8


# ---------------------------------------------------------------- FN-040
def produce_tw_settlement(ctx: RunContext) -> list[Event]:
    market_cal, _office_cal, missing = build_taifex_calendars(
        ctx.cfg, ctx.range_start, ctx.range_end
    )
    if missing:
        ctx.logger.warning("taifex holiday table missing years %s; weekends only", missing)
    return build_tw_settlement_events(ctx.cfg, ctx.range_start, ctx.range_end, market_cal)


# ---------------------------------------------------------------- FN-041
def produce_tw_earnings(ctx: RunContext) -> list[Event]:
    cfg = ctx.cfg
    logger = ctx.logger
    _market_cal, office_cal, missing = build_taifex_calendars(
        cfg, ctx.range_start, ctx.range_end
    )
    if missing:
        logger.warning("taifex holiday table missing years %s; weekends only", missing)

    mops = fetch_mops_ir(ctx.session, cfg, ctx.today)
    yahoo = fetch_yahoo_ir(ctx.session, cfg)
    for err in list(mops.errors) + list(yahoo.errors):
        logger.warning("tw-earnings fetch error: %s", err)
    try:
        _note_source(
            ctx, "mops",
            records=len(mops.records), errors=len(mops.errors),
            ok_scopes=len(mops.ok_scopes or ()),
            expected_scopes=len(cfg.mops.markets) * 2,
            error_samples=[str(e)[:200] for e in list(mops.errors)[:3]],
        )
        _note_source(
            ctx, "yahoo",
            enabled=bool(getattr(cfg.yahoo, "enabled", True)),
            records=len(yahoo.records), errors=len(yahoo.errors),
            error_samples=[str(e)[:200] for e in list(yahoo.errors)[:3]],
        )
    except Exception:  # noqa: BLE001  統計不得影響主流程
        pass

    merged = merge_ir_records(mops.records, yahoo.records)
    reconcile = cfg.mops.reconcile_future
    upsert_ir_records(
        ctx.conn,
        merged,
        ctx.now_utc,
        ctx.today,
        mops.ok_scopes if reconcile else None,
        mops.scope_counts if reconcile else None,
    )
    records = load_ir_records(ctx.conn, ctx.range_start, ctx.range_end)

    events = build_tw_ir_events(
        records, ZoneInfo(cfg.timezone), cfg.tw_ir.default_duration_minutes
    ) + build_tw_filing_events(cfg, ctx.range_start, ctx.range_end, office_cal)

    seen: set[str] = set()
    unique: list[Event] = []
    for ev in events:
        if ev.uid in seen:
            logger.warning("tw-earnings duplicate uid dropped: %s", ev.uid)
            continue
        seen.add(ev.uid)
        unique.append(ev)

    logger.info(
        "tw-earnings: MOPS %d records/%d errors, Yahoo %d records/%d errors, %d events",
        len(mops.records), len(mops.errors), len(yahoo.records), len(yahoo.errors), len(unique),
    )
    if mops.errors and yahoo.errors and not mops.records and not yahoo.records:
        logger.warning("tw-earnings: all sources failed; using accumulated store only")
    return unique


def _note_source(ctx, name: str, **stats) -> None:
    """FN-068：記錄資料來源統計；ctx 無 source_stats（如 SimpleNamespace）時靜默略過。"""
    d = getattr(ctx, "source_stats", None)
    if isinstance(d, dict):
        d[name] = stats


# ---------------------------------------------------------------- FN-042
def produce_us_earnings(
    ctx: RunContext,
    fetcher: Callable[[str, int, date], list[UsEarningRecord]] = fetch_us_earnings,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[Event], int, bool]:
    """有限並行抓取 watchlist；回傳 (events, hung_workers, degraded)。SQLite 只在主執行緒使用。

    degraded：本次成功抓取檔數 / watchlist 檔數 < DEGRADED_RATIO。降級時 us-earnings.ics 仍照常
    產出（未成功的 ticker 沿用累積庫舊資料）；呼叫端（main.run）把 "us-earnings:degraded"
    加入 failures（退出碼 1），但仍讓 F-008 同步：events 來自累積庫（load_us_earnings），
    未成功的 ticker 若庫中有舊資料，其未來事件仍在 events 中，故 plan_sync 不會把它們視為
    missing_future 而刪除。
    """
    cfg = ctx.cfg
    uc = cfg.us_earnings
    logger = ctx.logger
    tickers = list(uc.tickers)
    today_et = ctx.now_utc.astimezone(_ET).date()
    t0 = clock()
    deadline = t0 + float(uc.total_budget_sec)
    per_timeout = float(uc.per_ticker_timeout_sec)

    started_at: dict[str, float] = {}

    def worker(ticker: str) -> list[UsEarningRecord]:
        started_at[ticker] = clock()
        return fetcher(ticker, uc.earnings_dates_limit, today_et)

    futures: dict[str, Future] = {}
    all_futures: list[Future] = []  # 含暖機重試前逾時的 future，供 hung_workers 計算
    # outcome: ("ok", records) | ("timeout", None) | ("queued", None) | ("fail", exc)
    outcomes: dict[str, tuple[str, object]] = {}

    def wait_for(ticker: str) -> tuple[str, object]:
        fut = futures[ticker]
        while True:
            now = clock()
            if ticker in started_at:
                limit = min(started_at[ticker] + per_timeout, deadline)
            else:
                limit = deadline
            remaining = limit - now
            try:
                return ("ok", fut.result(timeout=max(0.0, min(remaining, _POLL_SEC))))
            except FutureTimeout:
                if clock() < limit:
                    continue
                if ticker not in started_at and fut.cancel():
                    return ("queued", None)
                return ("timeout", None)
            except Exception as e:  # noqa: BLE001
                return ("fail", e)

    ex = ThreadPoolExecutor(max_workers=int(uc.max_workers), thread_name_prefix="yf")
    ok = timeout = queued = failed = 0
    try:
        if tickers:
            # R9：暖機（第一檔）受 deadline 約束，結果沿用；
            # 第一次失敗/逾時且剩餘預算 >= per_timeout + 5 時，同 executor 重試一次
            first = tickers[0]
            for attempt in (1, 2):
                if attempt == 2 and deadline - clock() < per_timeout + _WARMUP_RETRY_MARGIN_SEC:
                    break
                if attempt == 2:
                    logger.warning("%s warm-up retry", first)
                fut = ex.submit(worker, first)
                futures[first] = fut
                all_futures.append(fut)
                try:
                    res = fut.result(timeout=max(0.0, min(per_timeout, deadline - clock())))
                    outcomes[first] = ("ok", res)
                    break
                except FutureTimeout:
                    logger.warning("%s warm-up timeout", first)
                    outcomes[first] = ("timeout", None)
                except Exception as e:  # noqa: BLE001
                    logger.warning("%s warm-up failed: %s", first, e)
                    outcomes[first] = ("fail", e)

            for ticker in tickers[1:]:
                futures[ticker] = ex.submit(worker, ticker)
                all_futures.append(futures[ticker])
                sleep(float(uc.submit_stagger_sec))

        for ticker in tickers:
            kind, payload = outcomes[ticker] if ticker in outcomes else wait_for(ticker)
            if kind == "ok":
                upsert_us_earnings(
                    ctx.conn, ticker, payload, ctx.now_utc, today_et, uc.reconcile_future
                )
                ok += 1
            elif kind == "timeout":
                logger.warning("%s timeout", ticker)
                futures[ticker].cancel()
                timeout += 1
            elif kind == "queued":
                logger.warning("%s cancelled while queued (budget exhausted)", ticker)
                queued += 1
            else:
                logger.warning("%s fetch failed: %s", ticker, payload)
                failed += 1
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    hung = sum(1 for f in all_futures if not f.done())

    records = load_us_earnings(ctx.conn, uc.tickers, ctx.range_start, ctx.range_end)
    events = build_us_earnings_events(records, uc.group_of)
    logger.info(
        "us-earnings: ok=%d timeout=%d queued_cancelled=%d failed=%d hung=%d in %.1fs",
        ok, timeout, queued, failed, hung, clock() - t0,
    )
    if tickers and ok == 0:
        logger.warning("us-earnings: all fetches failed; using accumulated store only")
    _note_source(ctx, "yfinance", total=len(tickers), ok=ok, timeout=timeout,
                 queued=queued, failed=failed)
    degraded = bool(tickers) and ok / len(tickers) < DEGRADED_RATIO
    if degraded:
        logger.warning(
            "us-earnings degraded: %d/%d fetched; missing tickers keep cached data",
            ok, len(tickers),
        )
    return events, hung, degraded


# ---------------------------------------------------------------- FN-043
def produce_us_settlement(ctx: RunContext) -> list[Event]:
    cal = build_nyse_calendar(ctx.range_start, ctx.range_end)
    return build_us_settlement_events(ctx.range_start, ctx.range_end, cal)


# ---------------------------------------------------------------- FN-065
def produce_global_holidays(
    ctx: RunContext,
    *,
    closures_fn: Callable[..., MarketClosures] = compute_market_closures,
    names_fn: Callable[..., tuple[dict, list[str]]] = lookup_holiday_names,
) -> tuple[list[Event], int, bool]:
    """海外休市事件。closures_fn 例外不攔截（交給 main：舊檔保留、F-008 不同步）。"""
    logger = ctx.logger
    closures: list[MarketClosures] = []
    names: dict = {}
    warnings: list[str] = []
    for spec in MARKET_SPECS:
        mc = closures_fn(spec, ctx.range_start, ctx.range_end)
        closures.append(mc)
        market_names, warns = names_fn(spec, list(mc.closed))
        names[spec.code] = market_names
        for w in warns:
            logger.warning("global-holidays %s: %s", spec.code, w)
            warnings.append(w)
        unnamed = sorted(d for d in mc.closed
                         if d not in market_names or market_names[d].source == "none")
        if unnamed:
            logger.info("global-holidays %s: unnamed closures %s", spec.code,
                        [d.isoformat() for d in unnamed])
    events = build_global_holiday_events(
        ctx.range_start, ctx.range_end, closures, names, ZoneInfo(ctx.cfg.timezone)
    )
    by = {mc.market: mc for mc in closures}

    def n(code: str, attr: str) -> int:
        return len(getattr(by[code], attr)) if code in by else 0

    logger.info(
        "global-holidays: us closed=%d early=%d, jp closed=%d, kr closed=%d, events=%d",
        n("us", "closed"), n("us", "early"), n("jp", "closed"), n("kr", "closed"), len(events),
    )
    return events, 0, bool(warnings)
