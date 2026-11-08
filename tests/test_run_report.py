"""FN-069：run_report 測試。"""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from core.run_report import REPORT_SCHEMA_VERSION, build_run_report, write_run_report

T0 = datetime(2026, 11, 9, 0, 31, 4, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 9, 0, 32, 40, tzinfo=timezone.utc)


def mk(**over):
    kw = dict(
        outcome="completed", exit_code=1, started_at_utc=T0, finished_at_utc=T1,
        today=date(2026, 11, 9),
        calendars={
            "tw-settlement": {"name": "台股結算", "filename": "tw-settlement.ics",
                              "status": "ok", "events": 110, "error": None},
            "global-holidays": {"name": "海外休市", "filename": "global-holidays.ics",
                                "status": "failed", "events": None, "error": "RuntimeError: x"},
        },
        sources={"mops": {"records": 0, "errors": 4, "ok_scopes": 0,
                          "expected_scopes": 4, "error_samples": ["HTTP 403"]}},
        failures=["global-holidays:failed"], platform="linux", apple_enabled=False,
    )
    kw.update(over)
    return build_run_report(**kw)


def test_fields_and_roundtrip(tmp_path):
    r = mk()
    assert r["schema_version"] == REPORT_SCHEMA_VERSION == 1
    assert r["started_at_utc"] == "2026-11-09T00:31:04Z"
    assert r["finished_at_utc"] == "2026-11-09T00:32:40Z"
    assert r["today"] == "2026-11-09"
    assert r["error"] is None and r["apple_enabled"] is False
    p = tmp_path / "run-report.json"
    write_run_report(p, r)
    back = json.loads(p.read_text(encoding="utf-8"))
    assert back == r
    assert back["calendars"]["global-holidays"]["status"] == "failed"
    assert back["sources"]["mops"]["errors"] == 4
    assert "台股結算" in p.read_text(encoding="utf-8")  # ensure_ascii=False


def test_today_none():
    assert mk(today=None, outcome="config_error", calendars={}, sources={})["today"] is None


def test_path_masking():
    root = Path("/opt/someone/proj")
    r = mk(error=f"OSError: cannot open {root}/data/x.db", prj_root=root,
           calendars={"a": {"status": "failed", "error": f"E: {root}/y"}})
    assert str(root) not in json.dumps(r)
    assert r["error"] == "OSError: cannot open ./data/x.db"
    assert r["calendars"]["a"]["error"] == "E: ./y"


def test_truncation():
    r = mk(error="E: " + "x" * 1000, calendars={"a": {"status": "failed", "error": "y" * 999}})
    assert len(r["error"]) == 300
    assert len(r["calendars"]["a"]["error"]) == 300


def test_invalid_outcome():
    with pytest.raises(ValueError):
        mk(outcome="bogus")


@pytest.mark.parametrize("o", ["completed", "offline_skipped", "config_error", "init_failed"])
def test_valid_outcomes(o):
    assert mk(outcome=o)["outcome"] == o


def test_write_atomic_no_leftover(tmp_path):
    p = tmp_path / "r.json"
    write_run_report(p, mk())
    write_run_report(p, mk(exit_code=0))
    assert [x.name for x in tmp_path.iterdir()] == ["r.json"]
    assert json.loads(p.read_text(encoding="utf-8"))["exit_code"] == 0
