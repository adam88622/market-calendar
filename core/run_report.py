"""FN-069：run-report.json 的組裝與原子寫入（v6 V6-3.5、R18-6 最小版）。"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path

from writer.atomic import write_atomic

REPORT_SCHEMA_VERSION = 1
_OUTCOMES = ("completed", "offline_skipped", "config_error", "init_failed")
_MAX_ERR = 300


def _fmt_utc(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_error(msg: str | None, prj_root: Path | None) -> str | None:
    if msg is None:
        return None
    s = str(msg)
    if prj_root is not None and str(prj_root):
        s = s.replace(str(prj_root), ".")
    return s[:_MAX_ERR]


def build_run_report(*, outcome: str, exit_code: int, started_at_utc: datetime,
                     finished_at_utc: datetime, today: date | None,
                     calendars: dict[str, dict], sources: dict[str, dict],
                     failures: list[str], platform: str, apple_enabled: bool | None,
                     error: str | None = None, prj_root: Path | None = None) -> dict:
    if outcome not in _OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome!r}")
    cals: dict[str, dict] = {}
    for cid, c in (calendars or {}).items():
        c = dict(c)
        c["error"] = _clean_error(c.get("error"), prj_root)
        cals[cid] = c
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "outcome": outcome,
        "exit_code": exit_code,
        "started_at_utc": _fmt_utc(started_at_utc),
        "finished_at_utc": _fmt_utc(finished_at_utc),
        "today": today.isoformat() if today is not None else None,
        "platform": platform,
        "apple_enabled": apple_enabled,
        "failures": list(failures),
        "error": _clean_error(error, prj_root),
        "calendars": cals,
        "sources": copy.deepcopy(sources) if sources else {},
    }


def write_run_report(path: Path, report: dict) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    write_atomic(Path(path), text.encode("utf-8"))
