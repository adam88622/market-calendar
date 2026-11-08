"""FN-067：apply_platform_overrides 純函式測試。

main 整合驗收（run() 在 linux、--authorize 在 win32）由後續 agent 追加於本檔「main 整合」段之後。
"""
import dataclasses

import pytest

from core.config import apply_platform_overrides, load_config


@pytest.fixture
def cfg_apple(fixtures_dir, prj_root):
    cfg = load_config(fixtures_dir / "config_test_apple.yaml", prj_root)
    assert cfg.apple_calendar.enabled is True
    return cfg


def test_darwin_enabled_same_object(cfg_apple):
    out, note = apply_platform_overrides(cfg_apple, "darwin")
    assert out is cfg_apple and note is None


def test_linux_enabled_disabled(cfg_apple):
    out, note = apply_platform_overrides(cfg_apple, "linux")
    assert out is not cfg_apple
    assert out.apple_calendar.enabled is False
    assert cfg_apple.apple_calendar.enabled is True  # 原物件未被改
    assert note and "non-macOS" in note and "linux" in note
    assert dataclasses.replace(out, apple_calendar=cfg_apple.apple_calendar) == cfg_apple
    assert out.apple_calendar.prefer_source == cfg_apple.apple_calendar.prefer_source


def test_win32_disabled_same_object(cfg_apple):
    cfg = dataclasses.replace(
        cfg_apple, apple_calendar=dataclasses.replace(cfg_apple.apple_calendar, enabled=False))
    out, note = apply_platform_overrides(cfg, "win32")
    assert out is cfg and note is None


# ---- main 整合（後續 agent 於此追加）----

import functools  # noqa: E402
import logging  # noqa: E402
from pathlib import Path  # noqa: E402

import main  # noqa: E402
import pipelines  # noqa: E402
from tests.test_integration_run import (  # noqa: E402,F401
    TODAY, T1, _cleanup_logging, mops_records, net, producers, write_config,
)


class _BoomAdapter:
    def __getattr__(self, name):
        raise AssertionError(f"apple adapter must not be used: {name}")


def test_main_linux_disables_apple(tmp_path, net, monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "linux")
    cfg = write_config(tmp_path, base="config_test_apple.yaml")
    prods = producers(net, {"global-holidays": pipelines.produce_global_holidays})
    rc = main.run(cfg, today=TODAY, now_utc=T1, producers=prods, apple_adapter=_BoomAdapter())
    assert rc == 0
    from icalendar import Calendar

    files = sorted((tmp_path / "output").glob("*.ics"))
    assert len(files) == 5
    for f in files:
        Calendar.from_ical(f.read_bytes())
    log = (tmp_path / "logs" / "run-20261108.log").read_text(encoding="utf-8")
    assert "non-macOS" in log and "apple calendar: disabled" in log


def test_main_win32_authorize_returns_2(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "win32")
    import apple.authorize as az

    def boom(*a, **k):
        raise AssertionError("authorize must not be called")

    monkeypatch.setattr(az, "authorize", boom)
    assert main.main(["--authorize"]) == 2
