from datetime import date

import pytest

from core.business_calendar import BusinessCalendar
from core.models import SettlementDate

CNY = {date(2026, 2, d): "春節" for d in (16, 17, 18, 19, 20)}
CNY[date(2026, 2, 12)] = "春節前結算交割日（無交易）"
CNY[date(2026, 2, 13)] = "春節前結算交割日（無交易）"


def cal(closed=None):
    return BusinessCalendar("期交所", closed if closed is not None else CNY)


def test_is_business_day_and_reason():
    c = cal()
    assert c.is_business_day(date(2026, 11, 18))
    assert not c.is_business_day(date(2026, 11, 14))  # 週六
    assert not c.is_business_day(date(2026, 2, 17))
    assert c.closed_reason(date(2026, 11, 14)) == "週末"
    assert c.closed_reason(date(2026, 2, 17)) == "春節"
    assert c.closed_reason(date(2026, 11, 18)) == ""


def test_weekend_not_closed():
    c = BusinessCalendar("X", {}, weekend_closed=False)
    assert c.is_business_day(date(2026, 11, 14))


def test_shift_business_day_untouched():
    r = cal().shift(date(2026, 11, 18), "next")
    assert r == SettlementDate(date(2026, 11, 18), date(2026, 11, 18), "")


def test_shift_friday_closed():
    c = cal({date(2026, 11, 13): "測試休市"})
    n = c.shift(date(2026, 11, 13), "next")
    assert n.adjusted == date(2026, 11, 16) and n.original == date(2026, 11, 13)
    assert n.reason == "期交所休市（測試休市）"
    p = c.shift(date(2026, 11, 13), "previous")
    assert p.adjusted == date(2026, 11, 12)


def test_shift_none():
    r = cal().shift(date(2026, 2, 18), "none")
    assert r.adjusted == date(2026, 2, 18) and r.reason == ""


def test_shift_spring_festival():
    r = cal().shift(date(2026, 2, 18), "next")
    assert r.adjusted == date(2026, 2, 23)
    assert "春節" in r.reason
    assert cal().shift(date(2026, 2, 18), "previous").adjusted == date(2026, 2, 11)


def test_shift_weekend_reason():
    r = cal().shift(date(2026, 11, 14), "next")
    assert r.adjusted == date(2026, 11, 16) and "週末" in r.reason


def test_shift_invalid_policy():
    with pytest.raises(ValueError):
        cal().shift(date(2026, 11, 18), "skip")  # type: ignore[arg-type]


def test_shift_runtime_error():
    closed = {date(2026, 3, 1) + __import__("datetime").timedelta(days=i): "長假" for i in range(60)}
    with pytest.raises(RuntimeError):
        cal(closed).shift(date(2026, 3, 2), "next")
