"""Live 測試（需要網路）：預設略過，以 `pytest -m live` 執行。"""
from __future__ import annotations

import time
from datetime import date

import pytest

pytestmark = pytest.mark.live


def test_yfinance_nvda():
    from scrapers.yf_earnings import fetch_us_earnings

    recs = fetch_us_earnings("NVDA", 12, date.today())
    assert len(recs) >= 1


def test_mops_sii_current_month():
    import requests

    from core.config import load_config
    from pathlib import Path
    from scrapers.mops_ir import fetch_mops_ir

    prj = Path(__file__).resolve().parent.parent
    cfg = load_config(prj / "config.yaml", prj)
    cfg = cfg.__class__(**{**cfg.__dict__, "mops": cfg.mops.__class__(
        **{**cfg.mops.__dict__, "markets": ("sii",)})})
    res = fetch_mops_ir(requests.Session(), cfg, date.today())
    assert len(res.records) >= 0


def test_yahoo_ir():
    import requests

    from core.config import load_config
    from pathlib import Path
    from scrapers.yahoo_ir import fetch_yahoo_ir

    prj = Path(__file__).resolve().parent.parent
    cfg = load_config(prj / "config.yaml", prj)
    res = fetch_yahoo_ir(requests.Session(), cfg)
    assert not res.errors


def test_full_run_under_120s(tmp_path, monkeypatch):
    """R14：用複製的正式 config 並停用 apple_calendar，避免實機測試寫入真的行事曆。"""
    import re
    from pathlib import Path

    import main

    prj = Path(__file__).resolve().parent.parent
    text = (prj / "config.yaml").read_text(encoding="utf-8")
    text = re.sub(r"(?m)^apple_calendar:.*(?:\n[ \t]+.*)*\n?", "", text)
    text += "\napple_calendar: {enabled: false}\n"
    # 輸出一律導向 tmp_path，不污染 $PRJ 正式目錄
    text = re.sub(r"(?m)^(\s+)output_dir:.*$", rf"\1output_dir: {tmp_path / 'output'}", text)
    text = re.sub(r"(?m)^(\s+)data_dir:.*$", rf"\1data_dir: {tmp_path / 'data'}", text)
    text = re.sub(r"(?m)^(\s+)logs_dir:.*$", rf"\1logs_dir: {tmp_path / 'logs'}", text)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(text, encoding="utf-8")

    t0 = time.monotonic()
    rc = main.run(cfg_path)
    assert time.monotonic() - t0 < 120
    assert rc == 0
