import json
import shutil
from datetime import datetime, timezone

from cloud.publish import main, publish_site

ICS = b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
EMPTY = b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
NOW = datetime(2026, 11, 9, 0, 33, tzinfo=timezone.utc)
IDS = {"tw-settlement": "tw-settlement.ics", "us-settlement": "us-settlement.ics",
       "tw-earnings": "tw-earnings.ics", "us-earnings": "us-earnings.ics",
       "global-holidays": "global-holidays.ics"}


def setup(tmp_path, prj_root):
    cfg = tmp_path / "config.yaml"
    txt = (prj_root / "config.cloud.yaml").read_text(encoding="utf-8")
    cfg.write_text(txt, encoding="utf-8")
    site = tmp_path / "site"
    site.mkdir()
    for fn in IDS.values():
        (site / fn).write_bytes(ICS)
    prev = tmp_path / "prev"
    prev.mkdir()
    return cfg, site, prev


def write_report(tmp_path, status="ok"):
    rep = {"outcome": "completed", "exit_code": 0, "finished_at_utc": "2026-11-09T00:32:40Z",
           "failures": [], "calendars": {c: {"status": status, "events": 1, "error": None} for c in IDS},
           "sources": {}}
    p = tmp_path / "run-report.json"
    p.write_text(json.dumps(rep), encoding="utf-8")
    return p


def test_publish_outputs(tmp_path, prj_root):
    cfg, site, prev = setup(tmp_path, prj_root)
    st = publish_site(cfg, write_report(tmp_path), prev, "https://x.github.io/r", 0, now_utc=NOW)
    assert (site / "index.html").is_file()
    disk = json.loads((site / "status.json").read_text(encoding="utf-8"))
    assert disk == st
    assert all(c["last_attempt_status"] == "ok" for c in st["calendars"])
    assert len(st["calendars"]) == 5 and st["run"]["report_missing"] is False


def test_no_github_output_health(tmp_path, prj_root, monkeypatch):
    cfg, site, prev = setup(tmp_path, prj_root)
    out = tmp_path / "gh_out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    publish_site(cfg, write_report(tmp_path), prev, "", 0, now_utc=NOW)
    assert not out.exists()


def test_missing_report_unknown(tmp_path, prj_root):
    cfg, site, prev = setup(tmp_path, prj_root)
    st = publish_site(cfg, tmp_path / "nope.json", prev, "", None, now_utc=NOW)
    assert st["run"]["report_missing"] is True
    assert all(c["last_attempt_status"] == "unknown" for c in st["calendars"])
    assert (site / "status.json").is_file()


def test_guard_restores_previous(tmp_path, prj_root):
    cfg, site, prev = setup(tmp_path, prj_root)
    (prev / "tw-earnings.ics").write_bytes(ICS)
    (site / "tw-earnings.ics").write_bytes(EMPTY)
    st = publish_site(cfg, write_report(tmp_path), prev, "", 0, now_utc=NOW)
    assert (site / "tw-earnings.ics").read_bytes() == ICS
    d = {c["id"]: c for c in st["calendars"]}
    assert d["tw-earnings"]["last_attempt_status"] == "guarded"


def test_cli_empty_exit_code(tmp_path, prj_root):
    cfg, site, prev = setup(tmp_path, prj_root)
    rc = main(["--config", str(cfg), "--report", str(write_report(tmp_path)),
               "--prev-dir", str(prev), "--restore-info", str(tmp_path / "none.json"),
               "--site-url", "https://x.github.io/r", "--exit-code", ""])
    assert rc == 0 and (site / "status.json").is_file()


def test_cli_bad_config(tmp_path):
    assert main(["--config", str(tmp_path / "missing.yaml"), "--prev-dir", str(tmp_path)]) == 1
