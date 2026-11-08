"""FN-066 整合測試：global-holidays 進入 run() / F-008 流程（離線）。"""
from __future__ import annotations

import functools
import logging

import pytest
from icalendar import Calendar

import pipelines
from tests.fake_calendar_adapter import FakeCalendarAdapter
from tests.test_integration_run import (  # noqa: F401  (fixtures)
    APPLE_CFG, CALS, T1, T2, _cleanup_logging, go, mops_records, net, outputs,
    producers, read_log, unfold, vevents, write_config,
)

ALL = CALS + ["global-holidays"]
GH_NAME = "海外休市（美日韓）"
ACCEPT = [
    "gh-us-closed-20261126", "gh-us-early-20261127", "gh-us-closed-20261225",
    "gh-jp-closed-20260921", "gh-jp-closed-20260922", "gh-jp-closed-20260923",
    "gh-jp-closed-20261103", "gh-kr-closed-20260924", "gh-kr-closed-20260925",
    "gh-kr-closed-20261009",
]


def gh_over(extra=None):
    d = {"global-holidays": pipelines.produce_global_holidays}
    d.update(extra or {})
    return d


def all_out(tmp):
    return {c: (tmp / "output" / f"{c}.ics").read_bytes() for c in ALL}


def boom_closures(spec, s, e):
    raise RuntimeError("pmc down")


def failing_gh():
    return functools.partial(pipelines.produce_global_holidays, closures_fn=boom_closures)


def test_gh_ics_content(tmp_path, net):
    cfg = write_config(tmp_path)
    assert go(cfg, net, T1, overrides=gh_over()) == 0
    b = (tmp_path / "output" / "global-holidays.ics").read_bytes()
    assert "X-WR-CALNAME:海外休市（美日韓）" in unfold(b)
    cal = Calendar.from_ical(b)
    evs = [c for c in cal.walk() if c.name == "VEVENT"]
    uids = [str(e["UID"]) for e in evs]
    assert len(uids) == len(set(uids)) > 0
    for u in ACCEPT:
        assert f"{u}@market-calendar-ics" in uids
    for e in evs:
        assert any(c.name == "VALARM" for c in e.walk())


def test_gh_second_run_bytes_equal(tmp_path, net):
    cfg = write_config(tmp_path)
    assert go(cfg, net, T1, overrides=gh_over()) == 0
    first = all_out(tmp_path)
    assert go(cfg, net, T2, overrides=gh_over()) == 0
    assert all_out(tmp_path) == first


def test_gh_failure_keeps_old(tmp_path, net):
    cfg = write_config(tmp_path)
    assert go(cfg, net, T1, overrides=gh_over()) == 0
    first = all_out(tmp_path)
    assert go(cfg, net, T2, overrides=gh_over({"global-holidays": failing_gh()})) == 1
    assert all_out(tmp_path) == first
    assert "global-holidays failed; previous file kept" in read_log(tmp_path)


def gh_events(fake):
    ident = next(c.identifier for c in fake.calendars.values() if c.title == GH_NAME)
    return fake.events_of(ident)


def test_gh_apple_sync_and_idempotent(tmp_path, net):
    fake = FakeCalendarAdapter()
    cfg = write_config(tmp_path, base=APPLE_CFG)
    assert go(cfg, net, T1, overrides=gh_over(), apple=fake) == 0
    titles = [c.title for c in fake.calendars.values()]
    assert GH_NAME in titles
    out = all_out(tmp_path)
    assert len(gh_events(fake)) == len(vevents(out["global-holidays"])) > 0
    fake.ops.clear()
    assert go(cfg, net, T2, overrides=gh_over(), apple=fake) == 0
    assert not {"create", "update", "delete", "create_calendar", "rename_calendar"} & set(
        fake.op_names())


def test_gh_apple_failure_no_delete(tmp_path, net):
    fake = FakeCalendarAdapter()
    cfg = write_config(tmp_path, base=APPLE_CFG)
    assert go(cfg, net, T1, overrides=gh_over(), apple=fake) == 0
    before = gh_events(fake)
    assert before
    fake.ops.clear()
    assert go(cfg, net, T2, overrides=gh_over({"global-holidays": failing_gh()}),
              apple=fake) == 1
    assert gh_events(fake) == before
    assert "delete" not in fake.op_names()
    assert sum(1 for o in fake.ops if o[0] == "commit") == 4


def test_gh_no_producer_skipped(tmp_path, net):
    fake = FakeCalendarAdapter()
    cfg = write_config(tmp_path, base=APPLE_CFG)
    assert go(cfg, net, T1, apple=fake) == 0
    assert "global-holidays: no producer supplied; skipped" in read_log(tmp_path)
    assert sum(1 for o in fake.ops if o[0] == "commit") == 4
