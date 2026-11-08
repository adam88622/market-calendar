import pytest

from core.uid import make_uid


def test_basic():
    assert make_uid("tw-earnings", "2330", "20261113") == "tw-earnings-2330-20261113@market-calendar-ics"


def test_deterministic_and_int_parts():
    assert make_uid("tw-monthly-settle", 202611) == make_uid("tw-monthly-settle", "202611")
    assert make_uid("us-earnings", "NVDA", 20261125).startswith("us-earnings-NVDA-20261125")


def test_event_accepts_uid():
    from datetime import date
    from core.models import Event
    Event(uid=make_uid("us-vix-settle", 202611), calendar="us-settlement", title="x",
          start=date(2026, 11, 18), all_day=True)


@pytest.mark.parametrize("parts", [("BRK.B",), ("a b",), ("",), ("中文",), ("a-b",)])
def test_bad_parts(parts):
    with pytest.raises(ValueError):
        make_uid("us-earnings", *parts)


def test_no_parts():
    with pytest.raises(ValueError):
        make_uid("tw-earnings")


@pytest.mark.parametrize("kind", ["", "bad kind", "a_b", "-x", "x-"])
def test_bad_kind(kind):
    with pytest.raises(ValueError):
        make_uid(kind, "1")
