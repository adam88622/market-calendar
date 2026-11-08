from datetime import date

import pytest

from core.dates import _add_months, compute_range, iter_months, nth_weekday


def test_nth_weekday_normal():
    assert nth_weekday(2026, 11, 2, 3) == date(2026, 11, 18)
    assert nth_weekday(2026, 11, 4, 3) == date(2026, 11, 20)
    assert nth_weekday(2026, 12, 4, 3) == date(2026, 12, 18)


def test_nth_weekday_boundary():
    assert nth_weekday(2027, 1, 4, 1) == date(2027, 1, 1)
    assert nth_weekday(2026, 9, 2, 3) == date(2026, 9, 16)
    assert nth_weekday(2026, 11, 6, 5) == date(2026, 11, 29)  # 第 5 個週日存在


def test_nth_weekday_errors():
    with pytest.raises(ValueError):
        nth_weekday(2026, 11, 2, 5)  # 11 月只有 4 個週三
    with pytest.raises(ValueError):
        nth_weekday(2026, 13, 2, 1)
    with pytest.raises(ValueError):
        nth_weekday(2026, 11, 7, 1)
    with pytest.raises(ValueError):
        nth_weekday(2026, 11, 2, 0)


def test_add_months():
    assert _add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert _add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert _add_months(date(2026, 1, 15), -2) == date(2025, 11, 15)


def test_compute_range_aligned():
    assert compute_range(date(2026, 11, 8), 3, 12, True) == (date(2026, 8, 1), date(2027, 11, 30))
    assert compute_range(date(2026, 12, 1), 3, 12) == (date(2026, 9, 1), date(2027, 12, 31))


def test_compute_range_not_aligned():
    assert compute_range(date(2026, 11, 8), 3, 12, False) == (date(2026, 8, 8), date(2027, 11, 8))


def test_compute_range_boundaries():
    assert compute_range(date(2027, 1, 31), 3, 12, False)[0] == date(2026, 10, 31)
    s, e = compute_range(date(2028, 2, 29), 3, 12, False)
    assert (s, e) == (date(2027, 11, 29), date(2029, 2, 28))
    assert compute_range(date(2028, 2, 29), 0, 0, True) == (date(2028, 2, 1), date(2028, 2, 29))


def test_compute_range_negative():
    with pytest.raises(ValueError):
        compute_range(date(2026, 11, 8), -1, 12)
    with pytest.raises(ValueError):
        compute_range(date(2026, 11, 8), 3, -1)


def test_iter_months():
    r = iter_months(date(2026, 8, 1), date(2027, 11, 30))
    assert len(r) == 16 and r[0] == (2026, 8) and r[-1] == (2027, 11)
    assert (2026, 12) in r and (2027, 1) in r
    assert iter_months(date(2026, 11, 3), date(2026, 11, 20)) == [(2026, 11)]
    assert iter_months(date(2026, 12, 1), date(2026, 11, 1)) == []
