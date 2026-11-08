"""FN-044：主程式。"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import requests

from core.config import apply_platform_overrides, load_config
from core.dates import compute_range
from core.logging_setup import setup_logging
from apple.adapter import CalendarAdapter
from core.models import CAL_ORDER, CalendarId, ConfigError, Event, RunContext
from storage.db import init_db
from writer.ics_writer import generate_calendar

SLOW_RUN_SEC = 120
APPLE_DEADLINE_SEC = 105  # v4.1 R12：apple_deadline = t_start + 105（固定）


def _default_producers() -> dict:
    import pipelines

    return {
        "tw-settlement": pipelines.produce_tw_settlement,
        "us-settlement": pipelines.produce_us_settlement,
        "tw-earnings": pipelines.produce_tw_earnings,
        "us-earnings": pipelines.produce_us_earnings,
        "global-holidays": pipelines.produce_global_holidays,
    }


def _force_exit(code: int) -> None:  # 獨立成函式以便測試替換
    os._exit(code)


def _config_failure(prj_root: Path, today: date, err: Exception) -> None:
    msg = f"config error: {err}"
    print(msg, file=sys.stderr)
    try:
        logs = prj_root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / f"run-{today.strftime('%Y%m%d')}.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} ERROR {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def _emit_report(report_path: Path | None, logger=None, **kw) -> None:
    """寫 run-report.json；任何失敗只記 WARNING，不改變結束碼。"""
    if report_path is None:
        return
    try:
        from core.run_report import build_run_report, write_run_report

        write_run_report(Path(report_path), build_run_report(**kw))
    except Exception as e:  # noqa: BLE001
        msg = f"run report write failed: {type(e).__name__}: {e}"
        if logger is not None:
            logger.warning(msg)
        else:
            print(msg, file=sys.stderr)


def _initial_cals(cfg, status: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for cid in CAL_ORDER:
        meta = cfg.calendars.get(cid)
        out[cid] = {
            "name": meta.name if meta else None,
            "filename": meta.filename if meta else None,
            "status": status, "events": None, "error": None,
        }
    return out


def run(
    config_path: Path | None = None,
    *,
    today: date | None = None,
    now_utc: datetime | None = None,
    producers: dict[CalendarId, Callable[[RunContext], list[Event] | tuple[list[Event], int] | tuple[list[Event], int, bool]]]
    | None = None,
    apple_adapter: CalendarAdapter | None = None,
    online_check: Callable[[], bool] | None = None,
    report_path: Path | None = None,
) -> int:
    t_start = time.monotonic()
    prj_root = Path(__file__).resolve().parent
    config_path = Path(config_path) if config_path else prj_root / "config.yaml"

    now_utc = now_utc or datetime.now(UTC).replace(microsecond=0)
    fallback_today = today or now_utc.astimezone(ZoneInfo("Asia/Taipei")).date()
    try:
        cfg = load_config(config_path, prj_root)
    except Exception as e:  # noqa: BLE001  含 ConfigError
        _config_failure(prj_root, fallback_today, e)
        _emit_report(
            report_path, outcome="config_error", exit_code=2, started_at_utc=now_utc,
            finished_at_utc=now_utc + timedelta(seconds=round(time.monotonic() - t_start)),
            today=fallback_today, calendars={}, sources={}, failures=[],
            platform=sys.platform, apple_enabled=None,
            error=f"{type(e).__name__}: {e}", prj_root=prj_root,
        )
        return 2
    today = today or now_utc.astimezone(ZoneInfo(cfg.timezone)).date()

    logger = setup_logging(cfg.paths.logs_dir, today, cfg.log.level, cfg.log.retention_days)
    cfg, note = apply_platform_overrides(cfg, sys.platform)
    if note:
        logger.info(note)
    if online_check is None and cfg.network_check.enabled:
        from core.netcheck import is_online

        urls = (cfg.mops.url, cfg.yahoo.url)
        timeout = cfg.network_check.timeout_sec
        online_check = lambda: is_online(urls, timeout)  # noqa: E731
    if online_check is not None:
        try:
            online = bool(online_check())
        except Exception:  # noqa: BLE001
            online = False
        if not online:
            logger.info("offline: network check failed; skipped this run "
                        "(no files or calendars changed)")
            _emit_report(
                report_path, logger, outcome="offline_skipped", exit_code=0,
                started_at_utc=now_utc,
                finished_at_utc=now_utc + timedelta(seconds=round(time.monotonic() - t_start)),
                today=today, calendars=_initial_cals(cfg, "not_run"), sources={},
                failures=[], platform=sys.platform,
                apple_enabled=cfg.apple_calendar.enabled, prj_root=prj_root,
            )
            return 0
    producers = producers if producers is not None else _default_producers()

    conn = None
    session = None
    failures: list[str] = []
    produced: dict[CalendarId, list[Event]] = {}
    hung_total = 0
    timings: dict[str, float] = {}
    ctx = None
    cal_results = _initial_cals(cfg, "not_run")
    try:
        t = time.monotonic()
        range_start, range_end = compute_range(
            today, cfg.range.months_before, cfg.range.months_after, cfg.range.align_to_month
        )
        conn = init_db(cfg.paths.data_dir / "calendar.db")
        session = requests.Session()
        ctx = RunContext(
            cfg=cfg, prj_root=prj_root, today=today, now_utc=now_utc,
            range_start=range_start, range_end=range_end,
            conn=conn, session=session, logger=logger,
        )
        timings["init"] = time.monotonic() - t
        logger.info("run start: today=%s range=%s..%s", today, range_start, range_end)

        for cal_id in CAL_ORDER:
            producer = producers.get(cal_id)
            if producer is None:
                logger.info("%s: no producer supplied; skipped", cal_id)
                cal_results[cal_id]["status"] = "skipped"
                continue
            t = time.monotonic()
            try:
                res = producer(ctx)
                if isinstance(res, tuple):
                    events, hung = res[0], res[1]
                    degraded = bool(res[2]) if len(res) > 2 else False
                else:
                    events, hung, degraded = res, 0, False
                ctx.hung_workers += hung
                t_prod = time.monotonic() - t
                generate_calendar(ctx, cal_id, events)
                produced[cal_id] = events  # 降級時仍同步 F-008（累積庫保有舊資料，不會誤刪）
                cal_results[cal_id].update(
                    status="degraded" if degraded else "ok", events=len(events))
                if degraded:
                    failures.append(f"{cal_id}:degraded")
                logger.info(
                    "%s: produce %.1fs, write %.1fs",
                    cal_id, t_prod, time.monotonic() - t - t_prod,
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(f"{cal_id} failed; previous file kept")
                cal_results[cal_id].update(
                    status="failed", events=None, error=f"{type(e).__name__}: {e}")
                failures.append(cal_id)
            timings[cal_id] = time.monotonic() - t
        hung_total = ctx.hung_workers

        # F-008：寫入 Apple 行事曆（在 conn.close() 之前；只同步 ics 成功的行事曆）
        if cfg.apple_calendar.enabled:
            t = time.monotonic()
            try:
                from apple.sync import sync_apple_calendars  # 延遲 import

                failures += sync_apple_calendars(
                    ctx, produced, apple_adapter, deadline=t_start + APPLE_DEADLINE_SEC
                )
            except Exception:  # noqa: BLE001
                logger.exception("apple calendar sync crashed")
                failures.append("apple:error")
            timings["apple"] = time.monotonic() - t
        else:
            logger.info("apple calendar: disabled")
    except Exception:  # noqa: BLE001  初始化階段失敗（DB 等）
        logger.exception("run initialization failed")
        failures.append("init")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass

    total = time.monotonic() - t_start
    stage = ", ".join(f"{k}={v:.1f}s" for k, v in timings.items())
    logger.info("run done in %.1fs (%s); failures=%s", total, stage, failures or "none")
    if total > SLOW_RUN_SEC:
        logger.warning("run took %.1fs, exceeding %ds", total, SLOW_RUN_SEC)

    code = 1 if failures else 0
    _emit_report(
        report_path, logger,
        outcome="init_failed" if "init" in failures else "completed",
        exit_code=code, started_at_utc=now_utc,
        finished_at_utc=now_utc + timedelta(seconds=round(total)),
        today=today, calendars=cal_results,
        sources=ctx.source_stats if ctx is not None else {},
        failures=failures, platform=sys.platform,
        apple_enabled=cfg.apple_calendar.enabled, prj_root=prj_root,
    )
    if hung_total > 0:
        logger.warning("%d yfinance workers still running at exit; forced exit", hung_total)
        logging.shutdown()
        _force_exit(code)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="main.py", description="market-calendar-ics")
    parser.add_argument(
        "--authorize", action="store_true",
        help="request Apple Calendar full access (triggers the macOS permission prompt) and exit",
    )
    parser.add_argument("--config", type=Path, default=None, help="path to config.yaml")
    parser.add_argument("--report", type=Path, default=None, help="write run-report.json to PATH")
    args = parser.parse_args(argv)
    if args.authorize:
        if sys.platform != "darwin":
            print("--authorize 僅支援 macOS", file=sys.stderr)
            return 2
        from apple.authorize import authorize

        return authorize()
    return run(args.config, report_path=args.report)


if __name__ == "__main__":
    sys.exit(main())
