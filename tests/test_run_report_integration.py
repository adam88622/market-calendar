"""FN-070：main 與 run-report.json 整合。"""
from __future__ import annotations

import json

import main
import pipelines
from tests.test_integration_run import (  # noqa: F401
    T1, TODAY, _cleanup_logging, go, mops_records, net, producers, write_config,
)


def rd(p):
    return json.loads(p.read_text(encoding="utf-8"))


def run_with(tmp_path, net, overrides=None, **kw):
    cfg = write_config(tmp_path)
    rp = tmp_path / "rep" / "run-report.json"
    rp.parent.mkdir(exist_ok=True)
    rc = main.run(cfg, today=TODAY, now_utc=T1, producers=producers(net, overrides),
                  report_path=rp, **kw)
    return rc, rp


def test_normal(tmp_path, net):
    rc, rp = run_with(tmp_path, net)
    r = rd(rp)
    assert rc == 0 and r["outcome"] == "completed" and r["exit_code"] == 0
    cals = r["calendars"]
    for c in ("tw-settlement", "us-settlement", "tw-earnings", "us-earnings"):
        assert cals[c]["status"] == "ok" and cals[c]["events"] > 0
    assert cals["global-holidays"]["status"] == "skipped"
    assert {"mops", "yahoo", "yfinance"} <= set(r["sources"])
    assert r["today"] == "2026-11-08" and r["started_at_utc"] == "2026-11-08T00:00:00Z"
    assert r["apple_enabled"] is False


def test_failed_calendar(tmp_path, net):
    def boom(ctx):
        raise RuntimeError("kaboom")

    rc, rp = run_with(tmp_path, net, {"us-settlement": boom})
    r = rd(rp)
    assert rc == 1 and r["exit_code"] == 1
    c = r["calendars"]["us-settlement"]
    assert c["status"] == "failed" and "RuntimeError" in c["error"]
    assert "us-settlement" in r["failures"]


def test_degraded(tmp_path, net):
    rc, rp = run_with(tmp_path, net, {"us-earnings": lambda ctx: ([], 0, True)})
    r = rd(rp)
    assert r["calendars"]["us-earnings"]["status"] == "degraded"
    assert "us-earnings:degraded" in r["failures"] and rc == 1


def test_offline(tmp_path, net):
    rc, rp = run_with(tmp_path, net, online_check=lambda: False)
    r = rd(rp)
    assert rc == 0 and r["outcome"] == "offline_skipped"
    assert all(c["status"] == "not_run" for c in r["calendars"].values())
    assert len(r["calendars"]) == 5


def test_config_error(tmp_path, net):
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: [valid\n", encoding="utf-8")
    rp = tmp_path / "r.json"
    rc = main.run(bad, today=TODAY, now_utc=T1, report_path=rp)
    assert rc == 2
    r = rd(rp)
    assert r["outcome"] == "config_error" and r["exit_code"] == 2 and r["calendars"] == {}
    assert r["error"]


def test_report_write_failure_keeps_exit_code(tmp_path, net):
    cfg = write_config(tmp_path)
    rc0 = main.run(cfg, today=TODAY, now_utc=T1, producers=producers(net))
    d = tmp_path / "isdir"
    d.mkdir()
    rc = main.run(cfg, today=TODAY, now_utc=T1, producers=producers(net), report_path=d)
    assert rc == rc0
    log = (tmp_path / "logs" / "run-20261108.log").read_text(encoding="utf-8")
    assert "WARNING" in log and "run report write failed" in log


def test_no_report_path_no_json(tmp_path, net):
    cfg = write_config(tmp_path)
    main.run(cfg, today=TODAY, now_utc=T1, producers=producers(net))
    assert not list(tmp_path.rglob("*.json"))


def test_hung_report_written_before_force_exit(tmp_path, net, monkeypatch):
    cfg = write_config(tmp_path)
    rp = tmp_path / "r.json"
    seen = []
    monkeypatch.setattr(main, "_force_exit", lambda c: seen.append((c, rp.exists())))
    rc = main.run(cfg, today=TODAY, now_utc=T1, report_path=rp,
                  producers=producers(net, {"us-earnings": lambda ctx: ([], 2)}))
    assert seen == [(0, True)] and rc == 0


def test_main_passes_report_path(tmp_path, monkeypatch):
    got = {}

    def fake_run(config, **kw):
        got.update(config=config, **kw)
        return 0

    monkeypatch.setattr(main, "run", fake_run)
    assert main.main(["--config", str(tmp_path / "c.yaml"), "--report", str(tmp_path / "r.json")]) == 0
    assert got["report_path"] == tmp_path / "r.json"
