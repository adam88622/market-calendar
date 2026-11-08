import io
from datetime import datetime

from apple.authorize import authorize
from tests.fake_calendar_adapter import FakeCalendarAdapter

NOW = lambda: datetime(2026, 11, 8, 21, 0, 5)  # noqa: E731


def run(fake, tmp_path):
    out = io.StringIO()
    rc = authorize(fake, out=out, log_dir=tmp_path, now=NOW, prj_root=tmp_path)
    return rc, out.getvalue()


def test_full_access(tmp_path):
    fake = FakeCalendarAdapter(status="fullAccess")
    rc, text = run(fake, tmp_path)
    assert rc == 0 and "fullAccess" in text
    assert ("request_full_access",) not in fake.ops


def test_not_determined_granted(tmp_path):
    fake = FakeCalendarAdapter(status="notDetermined")
    rc, text = run(fake, tmp_path)
    assert rc == 0 and "granted=True" in text
    assert ("request_full_access",) in fake.ops


def test_not_determined_denied(tmp_path):
    fake = FakeCalendarAdapter(status="notDetermined")
    fake.set_status("notDetermined", grant_on_request=False)
    rc, text = run(fake, tmp_path)
    assert rc == 1 and "granted=False" in text


def test_denied(tmp_path):
    fake = FakeCalendarAdapter(status="denied")
    rc, text = run(fake, tmp_path)
    assert rc == 1
    assert "隱私權與安全性" in text
    assert "tccutil reset Calendar" in text
    assert f'open "{tmp_path}/scheduler/MarketCalendarSync.app" --args --authorize' in text
    assert ("request_full_access",) not in fake.ops


def test_factory_failure(tmp_path, monkeypatch):
    # conftest autouse：EventKitAdapter.create 丟 RuntimeError
    out = io.StringIO()
    rc = authorize(None, out=out, log_dir=tmp_path, now=NOW, prj_root=tmp_path)
    assert rc == 2 and "EventKit" in out.getvalue()


def test_log_file_written(tmp_path):
    fake = FakeCalendarAdapter(status="denied")
    rc, text = run(fake, tmp_path)
    p = tmp_path / "authorize-20261108-210005.log"
    assert p.exists()
    body = p.read_text(encoding="utf-8")
    assert "denied" in body and "exit code: 1" in body
