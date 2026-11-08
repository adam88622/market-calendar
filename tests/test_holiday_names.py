"""FN-063 測試。"""
from __future__ import annotations

from datetime import date

import pytest

from core.holiday_names import lookup_holiday_names, translate_kr, translate_us
from core.models import MARKET_SPECS

SPEC = {s.code: s for s in MARKET_SPECS}


@pytest.mark.parametrize("name,exp", [
    ("Thanksgiving Day", "感恩節"),
    ("Thanksgiving", "感恩節"),
    ("Christmas Day (observed)", "聖誕節（補假）"),
    ("Washington's Birthday", "總統日"),
    ("Juneteenth National Independence Day", "六月節（解放日）"),
    ("Independence Day", "獨立紀念日"),
    ("Good Friday", "耶穌受難日"),
    ("Foo", None),
])
def test_translate_us(name, exp):
    assert translate_us(name) == exp


@pytest.mark.parametrize("name,exp", [
    ("The day preceding Chuseok", "中秋（秋夕）前一日"),
    ("Hangul Day", "韓文日"),
    ("Alternative holiday for Children's Day", "兒童節補休"),
    ("The second day of Chuseok", "中秋（秋夕）翌日"),
    ("Foo", None),
    ("The day preceding Foo", None),
])
def test_translate_kr(name, exp):
    assert translate_kr(name) == exp


def test_real_us():
    names, warns = lookup_holiday_names(SPEC["us"], [date(2026, 11, 26), date(2026, 12, 25)])
    assert names[date(2026, 11, 26)].display == "感恩節"
    assert names[date(2026, 11, 26)].source == "pandas_market_calendars"
    assert names[date(2026, 12, 25)].display == "聖誕節"
    assert warns == []


def test_real_jp():
    names, _ = lookup_holiday_names(
        SPEC["jp"], [date(2026, 11, 3), date(2026, 9, 22), date(2026, 9, 23), date(2027, 1, 2)])
    assert names[date(2026, 11, 3)].display == "文化の日"
    assert names[date(2026, 9, 22)].display == "国民の休日"
    assert names[date(2026, 9, 23)].display == "秋分の日"
    assert names[date(2027, 1, 2)].source == "builtin"


def test_real_kr():
    names, _ = lookup_holiday_names(
        SPEC["kr"], [date(2026, 9, 24), date(2026, 9, 25), date(2026, 10, 9), date(2026, 12, 31)])
    assert "中秋" in names[date(2026, 9, 24)].display
    assert "中秋" in names[date(2026, 9, 25)].display
    assert names[date(2026, 10, 9)].display == "韓文日"
    assert names[date(2026, 12, 31)].display == "年末休市"
    assert names[date(2026, 12, 31)].source == "builtin"


def _boom():
    raise ImportError("no holidays")


def test_importer_failure():
    names, w = lookup_holiday_names(SPEC["jp"], [date(2027, 1, 2)], importer=_boom)
    assert names[date(2027, 1, 2)].source == "builtin" and w
    names, w = lookup_holiday_names(SPEC["kr"], [date(2026, 10, 9)], importer=_boom)
    assert names[date(2026, 10, 9)].display == "休市"
    assert names[date(2026, 10, 9)].source == "none" and w
    names, w = lookup_holiday_names(SPEC["us"], [date(2026, 11, 26)], importer=_boom)
    assert names[date(2026, 11, 26)].display == "感恩節"
    assert names[date(2026, 11, 26)].source == "pandas_market_calendars" and w


def test_us_pmc_failure_falls_back_to_holidays():
    class Bad:
        @staticmethod
        def get_calendar(n):
            raise RuntimeError("x")
    names, w = lookup_holiday_names(SPEC["us"], [date(2026, 11, 26)], mcal_module=Bad)
    assert names[date(2026, 11, 26)].display == "感恩節"
    assert names[date(2026, 11, 26)].source == "holidays"
    assert w == []


def test_empty_days():
    assert lookup_holiday_names(SPEC["us"], []) == ({}, [])
