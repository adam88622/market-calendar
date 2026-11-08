"""FN-068：資料來源統計。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import main
import pipelines
from tests.test_integration_run import (  # noqa: F401
    T1, TODAY, _cleanup_logging, mops_records, net, producers, write_config,
)


def _run(tmp_path, net):
    cfg = write_config(tmp_path)
    rp = tmp_path / "rep.json"
    rc = main.run(cfg, today=TODAY, now_utc=T1, producers=producers(net), report_path=rp)
    return rc, json.loads(rp.read_text(encoding="utf-8"))


def test_stats_keys_and_values(tmp_path, net):
    rc, rep = _run(tmp_path, net)
    s = rep["sources"]
    assert set(s) == {"mops", "yahoo", "yfinance"}
    assert s["mops"]["records"] == len(net["mops"])
    assert s["mops"]["errors"] == 0 and s["mops"]["error_samples"] == []
    assert s["mops"]["expected_scopes"] % 2 == 0 and s["mops"]["expected_scopes"] >= 2
    assert s["yahoo"]["enabled"] in (True, False) and s["yahoo"]["records"] == 0
    y = s["yfinance"]
    assert y["total"] == 4 and y["ok"] == 4
    assert y["timeout"] == y["queued"] == y["failed"] == 0


def test_stats_fail_mode(tmp_path, net):
    net["fail"] = True
    rc, rep = _run(tmp_path, net)
    s = rep["sources"]
    assert s["mops"]["ok_scopes"] == 0 and s["mops"]["errors"] > 0
    assert s["mops"]["error_samples"] == ["mock mops down"]
    assert s["yahoo"]["errors"] > 0
    assert s["yfinance"]["failed"] == 4 and s["yfinance"]["ok"] == 0


def test_simplenamespace_ctx_no_error(tmp_path, net):
    ctx = SimpleNamespace()
    pipelines._note_source(ctx, "x", a=1)
    assert not hasattr(ctx, "source_stats")
    pipelines._note_source(SimpleNamespace(source_stats=None), "x", a=1)
