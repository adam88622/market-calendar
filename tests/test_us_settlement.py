"""FN-022、FN-035、FN-038 測試（含規則日期驗收）。"""
from datetime import date

import pytest

from core.holidays import build_nyse_calendar
from processors.us_settlement import (
    build_us_settlement_events,
    compute_us_monthly_expiry,
    compute_vix_settlement,
)


@pytest.fixture(scope="module")
def cal():
    return build_nyse_calendar(date(2024, 1, 1), date(2027, 12, 31))


def test_monthly_expiry(cal):
    assert compute_us_monthly_expiry(2026, 11, cal).adjusted == date(2026, 11, 20)
    assert compute_us_monthly_expiry(2026, 12, cal).adjusted == date(2026, 12, 18)
    sd = compute_us_monthly_expiry(2026, 6, cal)
    assert (sd.original, sd.adjusted) == (date(2026, 6, 19), date(2026, 6, 18))
    sd = compute_us_monthly_expiry(2025, 4, cal)
    assert (sd.original, sd.adjusted) == (date(2025, 4, 18), date(2025, 4, 17))


def test_vix_settlement(cal):
    assert compute_vix_settlement(2026, 11, cal).adjusted == date(2026, 11, 18)
    sd = compute_vix_settlement(2026, 12, cal)
    assert sd.adjusted == date(2026, 12, 16)  # base 2027-01-15
    sd = compute_vix_settlement(2026, 5, cal)
    assert sd.adjusted == date(2026, 5, 19)
    assert sd.adjusted.weekday() == 1
    assert "2026-06-18" in sd.reason and "NYSE 休市" in sd.reason
    sd = compute_vix_settlement(2024, 6, cal)
    assert sd.original == date(2024, 6, 19)
    assert sd.adjusted == date(2024, 6, 18)


def test_events_2026_11(cal):
    ev = build_us_settlement_events(date(2026, 11, 1), date(2026, 11, 30), cal)
    by = {e.uid.split("@")[0].rsplit("-", 1)[0]: e for e in ev}
    assert by["us-monthly-opex"].start == date(2026, 11, 20)
    assert by["us-vix-settle"].start == date(2026, 11, 18)
    assert by["us-vix-settle"].title == "VIX 期貨/選擇權結算"
    assert "2026-12" in by["us-vix-settle"].description
    assert by["us-monthly-opex"].uid == "us-monthly-opex-202611@market-calendar-ics"
    assert by["us-vix-settle"].uid == "us-vix-settle-202611@market-calendar-ics"
    assert all(e.all_day and e.source == "rule" for e in ev)


def test_quad_witching_2026(cal):
    ev = build_us_settlement_events(date(2026, 1, 1), date(2026, 12, 31), cal)
    quad = [e for e in ev if e.uid.startswith("us-quad-witching")]
    assert len(quad) == 4
    assert sorted(e.start.month for e in quad) == [3, 6, 9, 12]
    opex = [e for e in ev if e.uid.startswith("us-monthly-opex")]
    assert {int(e.uid.split("@")[0][-2:]) for e in opex}.isdisjoint({3, 6, 9, 12})
    assert len(opex) == 8
    assert not any(e.uid.startswith("us-monthly-opex-2026" + m) for e in ev for m in ("03", "06", "09", "12"))
    assert len({e.uid for e in ev}) == len(ev)


def test_quad_witching_june_juneteenth(cal):
    ev = build_us_settlement_events(date(2026, 6, 1), date(2026, 6, 30), cal)
    q = [e for e in ev if e.uid.startswith("us-quad-witching")]
    assert len(q) == 1 and q[0].start == date(2026, 6, 18)
    assert "同日為月選擇權到期" in q[0].description
    assert "Juneteenth" in q[0].description or "NYSE休市" in q[0].description


def test_events_range_boundary_by_adjusted(cal):
    # 2026-06 月選原定 6/19，調整為 6/18；範圍只到 6/18 仍應包含
    ev = build_us_settlement_events(date(2026, 6, 1), date(2026, 6, 18), cal)
    assert any(e.uid.startswith("us-quad-witching") for e in ev)
