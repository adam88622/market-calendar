from datetime import date

import pytest

from core.business_calendar import BusinessCalendar
from core.config import load_config
from processors.tw_earnings import build_tw_filing_events


@pytest.fixture(scope="module")
def cfg(fixtures_dir, prj_root):
    return load_config(fixtures_dir / "config_test.yaml", prj_root)


@pytest.fixture(scope="module")
def cal():
    return BusinessCalendar("非上班日", {
        date(2026, 10, 10): "國慶日",
        date(2027, 2, 8): "春節", date(2027, 2, 9): "春節", date(2027, 2, 10): "春節",
    })


def by_uid(evs):
    return {e.uid.split("@")[0]: e for e in evs}


def test_q3_2026_shift(cfg, cal):
    evs = by_uid(build_tw_filing_events(cfg, date(2026, 8, 1), date(2027, 11, 30), cal))
    e = evs["tw-filing-q3-2026"]
    assert e.start == date(2026, 11, 16) and e.all_day
    assert "原定 2026-11-14（六）→ 調整 2026-11-16（一）" in e.description
    assert e.title == "Q3 財報法定截止日（2026Q3）"
    assert e.source == "statute"


def test_revenue_oct(cfg, cal):
    evs = by_uid(build_tw_filing_events(cfg, date(2026, 8, 1), date(2027, 11, 30), cal))
    e = evs["tw-revenue-deadline-202610"]
    assert e.start == date(2026, 10, 12)
    assert e.title == "月營收申報截止（申報 9 月營收）"
    assert "非上班日（國慶日）" in e.description
    assert evs["tw-revenue-deadline-202611"].title == "月營收申報截止（申報 10 月營收）"
    assert evs["tw-revenue-deadline-202701"].title == "月營收申報截止（申報 12 月營收）"


def test_annual(cfg, cal):
    evs = by_uid(build_tw_filing_events(cfg, date(2026, 8, 1), date(2027, 11, 30), cal))
    e = evs["tw-filing-annual-2026"]
    assert e.start == date(2027, 3, 31)
    assert e.title == "年報法定截止日（2026 年度）"


def test_counts(cfg, cal):
    evs = build_tw_filing_events(cfg, date(2026, 8, 1), date(2027, 11, 30), cal)
    rev = [e for e in evs if "tw-revenue-deadline" in e.uid]
    other = [e for e in evs if "tw-revenue-deadline" not in e.uid]
    assert len(other) == 6 and len(rev) == 16


def test_spring_festival(cfg, cal):
    evs = by_uid(build_tw_filing_events(cfg, date(2026, 8, 1), date(2027, 11, 30), cal))
    assert evs["tw-revenue-deadline-202702"].start == date(2027, 2, 11)


def test_range_uses_adjusted_date(cfg, cal):
    evs = build_tw_filing_events(cfg, date(2026, 11, 15), date(2026, 11, 15), cal)
    assert evs == []
