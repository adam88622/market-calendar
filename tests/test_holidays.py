"""FN-018、FN-019 測試。"""
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from core.config import HolidayEntry, load_config
from core.holidays import build_nyse_calendar, build_taifex_calendars

PRJ = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def cfg():
    return load_config(PRJ / "config.yaml", PRJ)


def test_market_vs_office_calendar(cfg):
    market, office, missing = build_taifex_calendars(cfg, date(2026, 8, 1), date(2027, 11, 30))
    assert not market.is_business_day(date(2026, 2, 12))   # 春節前結算交割日
    assert office.is_business_day(date(2026, 2, 12))       # 機關仍上班
    assert not office.is_business_day(date(2026, 2, 16))   # 春節
    assert not market.is_business_day(date(2026, 2, 16))
    assert office.name == "非上班日"
    assert market.name == "期交所"
    assert missing == []


def test_extra_holiday_applies(cfg):
    extra = (HolidayEntry(date=date(2026, 11, 11), name="颱風假"),)
    c2 = replace(cfg, taifex_extra_holidays=extra)
    market, office, _ = build_taifex_calendars(c2, date(2026, 8, 1), date(2027, 11, 30))
    assert not market.is_business_day(date(2026, 11, 11))
    assert not office.is_business_day(date(2026, 11, 11))
    assert market.closed_reason(date(2026, 11, 11)) == "颱風假"


def test_extra_overrides_base_on_same_date(cfg):
    extra = (HolidayEntry(date=date(2026, 2, 12), name="補假", kind="holiday"),)
    c2 = replace(cfg, taifex_extra_holidays=extra)
    market, office, _ = build_taifex_calendars(c2, date(2026, 8, 1), date(2026, 12, 31))
    assert market.closed_reason(date(2026, 2, 12)) == "補假"
    assert not office.is_business_day(date(2026, 2, 12))


def test_missing_years(cfg):
    _, _, missing = build_taifex_calendars(cfg, date(2026, 8, 1), date(2028, 12, 31))
    assert missing == [2028]


def test_nyse_closed_days():
    cal = build_nyse_calendar(date(2024, 1, 1), date(2026, 12, 31))
    assert not cal.is_business_day(date(2026, 6, 19))   # Juneteenth
    assert "Juneteenth" in cal.closed_reason(date(2026, 6, 19))
    assert not cal.is_business_day(date(2025, 4, 18))   # Good Friday
    assert not cal.is_business_day(date(2024, 6, 19))
    assert cal.is_business_day(date(2026, 11, 20))
    assert not cal.is_business_day(date(2026, 11, 21))  # 週末
    assert cal.name == "NYSE"
