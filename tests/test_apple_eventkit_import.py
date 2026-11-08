"""FN-055 離線測試：import 不建 store、狀態對應、pytest 防護。"""
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

from apple import eventkit_adapter as mod
from apple.adapter import CalendarAdapter
from apple.eventkit_adapter import EventKitAdapter, _status_name

PRJ = Path(__file__).resolve().parent.parent


def test_import_does_not_load_eventkit():
    code = (
        "import sys; sys.path.insert(0, %r); import apple.eventkit_adapter; "
        "assert 'EventKit' not in sys.modules and 'Foundation' not in sys.modules"
    ) % str(PRJ)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_no_toplevel_eventkit_import_in_source():
    for line in Path(mod.__file__).read_text().splitlines():
        assert not line.startswith(("import EventKit", "from EventKit", "import Foundation", "from Foundation"))


@pytest.mark.parametrize(
    "value,name",
    [(0, "notDetermined"), (1, "restricted"), (2, "denied"), (3, "fullAccess"),
     (4, "writeOnly"), (5, "denied"), (-1, "denied"), (99, "denied"), (None, "denied")],
)
def test_status_name(value, name):
    assert _status_name(value) == name


def test_create_guard_under_pytest(monkeypatch):
    monkeypatch.delenv("MCI_EVENTKIT_TESTS", raising=False)
    with pytest.raises(RuntimeError):
        EventKitAdapter.create()
    monkeypatch.setenv("MCI_EVENTKIT_TESTS", "0")
    with pytest.raises(RuntimeError):
        EventKitAdapter.create()


def test_structurally_matches_protocol():
    assert issubclass(EventKitAdapter, CalendarAdapter)


def test_create_accepts_tz_name_param():
    assert "tz_name" in inspect.signature(EventKitAdapter.create).parameters
