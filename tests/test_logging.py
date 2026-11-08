import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging
import os
from datetime import date, timedelta
import pytest
from core.logging_setup import setup_logging


@pytest.fixture(autouse=True)
def _clean():
    yield
    lg = logging.getLogger("market_calendar")
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()


def _mine(lg):
    return [h for h in lg.handlers if getattr(h, "_mci_handler", False)]


def test_creates_and_writes(tmp_path):
    d = tmp_path / "logs"
    lg = setup_logging(d, date(2026, 11, 8))
    assert lg.name == "market_calendar"
    lg.info("hello")
    for h in lg.handlers:
        h.flush()
    txt = (d / "run-20261108.log").read_text(encoding="utf-8")
    assert "INFO [market_calendar] hello" in txt


def test_purges_old(tmp_path):
    today = date(2026, 11, 8)
    old = tmp_path / f"run-{(today - timedelta(days=40)).strftime('%Y%m%d')}.log"
    new = tmp_path / f"run-{(today - timedelta(days=5)).strftime('%Y%m%d')}.log"
    other = tmp_path / "keep.txt"
    for p in (old, new, other):
        p.write_text("x")
    setup_logging(tmp_path, today)
    assert not old.exists() and new.exists() and other.exists()


def test_idempotent(tmp_path):
    lg = setup_logging(tmp_path, date(2026, 11, 8))
    n = len(_mine(lg))
    lg = setup_logging(tmp_path, date(2026, 11, 8))
    assert len(_mine(lg)) == n


def test_stderr_handler_only_when_tty(tmp_path, monkeypatch):
    import sys
    class Fake:
        def __init__(self, tty): self.tty = tty
        def isatty(self): return self.tty
        def write(self, s): pass
        def flush(self): pass
    monkeypatch.setattr(sys, "stderr", Fake(False))
    assert len(_mine(setup_logging(tmp_path, date(2026, 11, 8)))) == 1
    monkeypatch.setattr(sys, "stderr", Fake(True))
    assert len(_mine(setup_logging(tmp_path, date(2026, 11, 8)))) == 2


def test_truncate_launchd_logs(tmp_path):
    big = tmp_path / "launchd.out.log"
    small = tmp_path / "launchd.err.log"
    big.write_bytes(b"a" * (1024 * 1024 + 1))
    small.write_bytes(b"b" * 100)
    setup_logging(tmp_path, date(2026, 11, 8))
    assert big.stat().st_size == 0
    assert small.stat().st_size == 100
