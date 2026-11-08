import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
import pytest
from writer.alarm import format_alarm_trigger

TZ = ZoneInfo("Asia/Taipei")
D = date(2026, 11, 20)


def dt(h, m=0):
    return datetime(2026, 11, 20, h, m, tzinfo=TZ)


def test_all_day():
    assert format_alarm_trigger(D, True, 1, time(20, 0), TZ) == "-PT4H"
    assert format_alarm_trigger(D, True, 2, time(9, 0), TZ) == "-PT39H"
    assert format_alarm_trigger(D, True, 0, time(8, 0), TZ) == "PT8H"


def test_timed():
    assert format_alarm_trigger(dt(14), False, 1, time(20, 0), TZ) == "-PT18H"
    assert format_alarm_trigger(dt(14, 30), False, 1, time(20, 0), TZ) == "-PT18H30M"
    assert format_alarm_trigger(dt(21), False, 1, time(20, 0), TZ) == "-PT25H"


def test_timed_other_tz_converted():
    s = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)  # 14:00 Taipei
    assert format_alarm_trigger(s, False, 1, time(20, 0), TZ) == "-PT18H"


def test_zero_and_minutes_only():
    assert format_alarm_trigger(dt(8), False, 0, time(8, 0), TZ) == "PT0M"
    assert format_alarm_trigger(dt(20, 30), False, 0, time(20, 0), TZ) == "-PT30M"


def test_naive_timed_raises():
    with pytest.raises(ValueError):
        format_alarm_trigger(datetime(2026, 11, 20, 14), False, 1, time(20, 0), TZ)
