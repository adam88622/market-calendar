import json
import sqlite3

import pytest

from cloud.state import StateError, calendar_files, main, restore_state, save_state
from core.config import load_config
from storage.db import init_db

FN = ["a.ics", "b.ics"]
ICS = b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"


def mk_db(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(p)
    return conn


def dirs(tmp_path):
    return (tmp_path / "state", tmp_path / "site", tmp_path / "data", tmp_path / "prev")


def test_restore_fresh(tmp_path):
    st, site, data, prev = dirs(tmp_path)
    r = restore_state(st, site, data, prev, FN)
    assert r == {"db": "fresh", "ics_restored": [], "status_restored": False}
    assert prev.is_dir() and list(prev.iterdir()) == []


def test_restore_ok_and_roundtrip(tmp_path):
    st, site, data, prev = dirs(tmp_path)
    (st / "site").mkdir(parents=True)
    (st / "site" / "a.ics").write_bytes(ICS)
    (st / "site" / "status.json").write_text("{}")
    mk_db(st / "data" / "calendar.db").close()
    r = restore_state(st, site, data, prev, FN)
    assert r["db"] == "restored" and r["ics_restored"] == ["a.ics"] and r["status_restored"]
    assert (site / "a.ics").read_bytes() == ICS == (prev / "a.ics").read_bytes()
    assert (data / "calendar.db").is_file()
    assert not (data / ".calendar.db.restore").exists()


def test_corrupt_db(tmp_path):
    st, site, data, prev = dirs(tmp_path)
    (st / "data").mkdir(parents=True)
    (st / "data" / "calendar.db").write_bytes(b"\x00garbage" * 500)
    r = restore_state(st, site, data, prev, FN)
    assert r["db"] == "corrupt_discarded"
    assert not (data / "calendar.db").exists()
    assert list(data.iterdir()) == []


def test_non_vcalendar_and_bad_status_skipped(tmp_path):
    st, site, data, prev = dirs(tmp_path)
    (st / "site").mkdir(parents=True)
    (st / "site" / "a.ics").write_bytes(b"<html>")
    (st / "site" / "status.json").write_text("not json")
    r = restore_state(st, site, data, prev, FN)
    assert r["ics_restored"] == [] and not r["status_restored"]
    assert not (site / "a.ics").exists()


def test_existing_target_requires_overwrite(tmp_path):
    st, site, data, prev = dirs(tmp_path)
    mk_db(data / "calendar.db").close()
    with pytest.raises(StateError):
        restore_state(st, site, data, prev, FN)
    restore_state(st, site, data, prev, FN, overwrite=True)
    site.mkdir(exist_ok=True)
    (site / "b.ics").write_bytes(ICS)
    (data / "calendar.db").unlink()
    with pytest.raises(StateError):
        restore_state(st, site, data, prev, FN)


def test_prev_dir_guard(tmp_path):
    st, site, data, prev = dirs(tmp_path)
    prev.mkdir()
    (prev / "precious.txt").write_text("x")
    with pytest.raises(StateError):
        restore_state(st, site, data, prev, FN)
    assert (prev / "precious.txt").exists()
    (prev / "precious.txt").unlink()
    (prev / "sub").mkdir()
    with pytest.raises(StateError):
        restore_state(st, site, data, prev, FN)
    assert (prev / "sub").exists()


def test_prev_dir_cleared_when_allowed(tmp_path):
    st, site, data, prev = dirs(tmp_path)
    prev.mkdir()
    (prev / "a.ics").write_bytes(b"old")
    (prev / "status.json").write_text("{}")
    restore_state(st, site, data, prev, FN)
    assert list(prev.iterdir()) == []


def test_save_wal_and_roundtrip(tmp_path):
    st, site, data, prev = dirs(tmp_path)
    conn = mk_db(data / "calendar.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.execute("CREATE TABLE zz(x)")
    conn.execute("INSERT INTO zz VALUES (42)")
    conn.commit()
    site.mkdir()
    (site / "a.ics").write_bytes(ICS)
    (site / "status.json").write_text("{}")
    (site / "index.html").write_text("<html>")
    r = save_state(st, site, data, FN)
    assert sorted(r["files"]) == ["a.ics", "index.html", "status.json"]
    assert (st / "README.md").is_file()
    names = {p.name for p in (st / "data").iterdir()}
    assert names == {"calendar.db"}
    c2 = sqlite3.connect(st / "data" / "calendar.db")
    assert c2.execute("SELECT x FROM zz").fetchone()[0] == 42
    c2.close()
    conn.close()
    # restore 往返
    t = tmp_path / "t"
    r2 = restore_state(st, t / "site", t / "data", t / "prev", FN)
    assert r2["db"] == "restored" and r2["ics_restored"] == ["a.ics"] and r2["status_restored"]
    assert (t / "site" / "a.ics").read_bytes() == ICS


def test_save_errors(tmp_path):
    st, site, data, prev = dirs(tmp_path)
    with pytest.raises(StateError, match="no database"):
        save_state(st, site, data, FN)
    mk_db(data / "calendar.db").close()
    st.mkdir()
    (st / "x").write_text("1")
    with pytest.raises(StateError):
        save_state(st, site, data, FN)
    (st / "x").unlink()
    with pytest.raises(StateError):
        save_state(st, site, data, FN, max_db_bytes=10)


def _cfg(tmp_path):
    prj = tmp_path / "prj"
    prj.mkdir()
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "config.yaml"
    text = src.read_text(encoding="utf-8")
    import yaml
    raw = yaml.safe_load(text)
    raw["paths"] = {"output_dir": "site", "data_dir": "cloud-data", "logs_dir": "cloud-logs"}
    cfgp = prj / "c.yaml"
    cfgp.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return prj, cfgp


def test_calendar_files_and_cli(tmp_path, capsys):
    prj, cfgp = _cfg(tmp_path)
    cfg = load_config(cfgp, prj)
    names = calendar_files(cfg)
    assert len(names) == 5 and names[0] == cfg.calendars["tw-settlement"].filename
    sd = tmp_path / "state"
    # restore on empty state
    info = tmp_path / "info.json"
    rc = main(["restore", "--config", str(cfgp), "--state-dir", str(sd),
               "--prev-dir", str(tmp_path / "prev"), "--info-out", str(info)])
    assert rc == 0 and json.loads(info.read_text())["db"] == "fresh"
    # second restore without --overwrite: no db yet -> still fine; create db then fail
    mk_db(prj / "cloud-data" / "calendar.db").close()
    assert main(["restore", "--config", str(cfgp), "--state-dir", str(sd),
                 "--prev-dir", str(tmp_path / "prev")]) == 1
    assert main(["restore", "--config", str(cfgp), "--state-dir", str(sd),
                 "--prev-dir", str(tmp_path / "prev"), "--overwrite"]) == 0
    mk_db(prj / "cloud-data" / "calendar.db").close()
    out = tmp_path / "payload"
    assert main(["save", "--config", str(cfgp), "--state-dir", str(out)]) == 0
    assert (out / "data" / "calendar.db").is_file()
    assert main(["save", "--config", str(cfgp), "--state-dir", str(out)]) == 1
    assert main(["save", "--config", str(tmp_path / "nope.yaml"), "--state-dir", str(tmp_path / "o2")]) == 1
