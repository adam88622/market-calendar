"""FN-071、FN-072：雲端狀態還原與保存（state 分支 payload）。"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from core.config import AppConfig, ConfigError, load_config
from core.models import CAL_ORDER

DB_NAME = "calendar.db"
MAX_USER_VERSION = 2
README_TEXT = (
    "此分支由 workflow 自動維護，請勿手動修改；刪除本分支 = 重置累積資料。\n"
)
_ALLOWED_PREV = {"status.json"}


class StateError(Exception):
    pass


def calendar_files(cfg: AppConfig) -> list[str]:
    return [cfg.calendars[c].filename for c in CAL_ORDER]


def _warn(msg: str) -> None:
    print(f"::warning::{msg}", flush=True)


def _check_db(path: Path) -> bool:
    try:
        conn = sqlite3.connect(str(path))
        try:
            res = conn.execute("PRAGMA integrity_check").fetchone()
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
        return bool(res) and res[0] == "ok" and ver <= MAX_USER_VERSION
    except sqlite3.Error:
        return False


def restore_state(state_dir: Path, site_dir: Path, data_dir: Path, prev_dir: Path,
                  filenames: list[str], *, overwrite: bool = False) -> dict:
    state_dir, site_dir = Path(state_dir), Path(site_dir)
    data_dir, prev_dir = Path(data_dir), Path(prev_dir)
    allowed_prev = set(filenames) | _ALLOWED_PREV

    if not overwrite:
        if (data_dir / DB_NAME).exists() or any((site_dir / f).exists() for f in filenames):
            raise StateError(
                "target already contains data (calendar.db or ics); use overwrite to replace"
            )
    # R18-4：prev_dir 含 ics／status.json 以外任何東西 → 拒絕，不刪除
    if prev_dir.exists():
        if not prev_dir.is_dir():
            raise StateError(f"prev_dir is not a directory: {prev_dir}")
        extra = [p.name for p in prev_dir.iterdir() if p.name not in allowed_prev]
        if extra:
            raise StateError(f"prev_dir contains unexpected entries, refusing to delete: {sorted(extra)}")
        for p in prev_dir.iterdir():
            if p.is_dir():  # 同名子目錄也視為異常
                raise StateError(f"prev_dir contains a directory: {p.name}")
        for p in list(prev_dir.iterdir()):
            p.unlink()
    prev_dir.mkdir(parents=True, exist_ok=True)
    site_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    db_status = "fresh"
    src_db = state_dir / "data" / DB_NAME
    if src_db.is_file():
        tmp = data_dir / f".{DB_NAME}.restore"
        shutil.copyfile(src_db, tmp)
        if _check_db(tmp):
            os.replace(tmp, data_dir / DB_NAME)
            db_status = "restored"
        else:
            tmp.unlink(missing_ok=True)
            db_status = "corrupt_discarded"
            _warn("state calendar.db failed integrity/version check; discarded")

    restored: list[str] = []
    for fn in filenames:
        src = state_dir / "site" / fn
        if not src.is_file():
            continue
        data = src.read_bytes()
        if not data.startswith(b"BEGIN:VCALENDAR"):
            _warn(f"state file {fn} is not a VCALENDAR; skipped")
            continue
        (site_dir / fn).write_bytes(data)
        (prev_dir / fn).write_bytes(data)
        restored.append(fn)

    status_restored = False
    st = state_dir / "site" / "status.json"
    if st.is_file():
        try:
            raw = st.read_bytes()
            json.loads(raw.decode("utf-8"))
            (prev_dir / "status.json").write_bytes(raw)
            status_restored = True
        except (ValueError, OSError):
            _warn("state status.json invalid; skipped")

    return {"db": db_status, "ics_restored": restored, "status_restored": status_restored}


def save_state(state_dir: Path, site_dir: Path, data_dir: Path, filenames: list[str],
               *, max_db_bytes: int = 90_000_000) -> dict:
    state_dir, site_dir, data_dir = Path(state_dir), Path(site_dir), Path(data_dir)
    if state_dir.exists() and any(state_dir.iterdir()):
        raise StateError(f"state_dir is not empty: {state_dir}")
    db = data_dir / DB_NAME
    if not db.is_file():
        raise StateError("no database to save")
    out_db = state_dir / "data" / DB_NAME
    out_db.parent.mkdir(parents=True, exist_ok=True)
    try:
        src = sqlite3.connect(str(db))
        try:
            src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            dst = sqlite3.connect(str(out_db))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        chk = sqlite3.connect(str(out_db))
        try:
            res = chk.execute("PRAGMA integrity_check").fetchone()
        finally:
            chk.close()
    except sqlite3.Error as e:
        raise StateError(f"database backup failed: {e}") from e
    if not res or res[0] != "ok":
        raise StateError("backup integrity_check failed")
    for suffix in ("-wal", "-shm"):
        Path(str(out_db) + suffix).unlink(missing_ok=True)
    size = out_db.stat().st_size
    if size > max_db_bytes:
        raise StateError(f"database too large: {size} > {max_db_bytes}")

    files: list[str] = []
    (state_dir / "site").mkdir(parents=True, exist_ok=True)
    for fn in [*filenames, "status.json", "index.html"]:
        s = site_dir / fn
        if s.is_file():
            shutil.copyfile(s, state_dir / "site" / fn)
            files.append(fn)
    (state_dir / "README.md").write_text(README_TEXT, encoding="utf-8")
    return {"db_bytes": size, "files": files}


def _load(config: str) -> AppConfig:
    p = Path(config).resolve()
    return load_config(p, p.parent)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cloud.state")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("restore")
    r.add_argument("--config", required=True)
    r.add_argument("--state-dir", required=True)
    r.add_argument("--prev-dir", required=True)
    r.add_argument("--info-out")
    r.add_argument("--overwrite", action="store_true")
    s = sub.add_parser("save")
    s.add_argument("--config", required=True)
    s.add_argument("--state-dir", required=True)
    args = ap.parse_args(argv)
    try:
        cfg = _load(args.config)
        names = calendar_files(cfg)
        if args.cmd == "restore":
            info = restore_state(Path(args.state_dir), cfg.paths.output_dir, cfg.paths.data_dir,
                                 Path(args.prev_dir), names, overwrite=args.overwrite)
            if args.info_out:
                Path(args.info_out).write_text(
                    json.dumps(info, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        else:
            info = save_state(Path(args.state_dir), cfg.paths.output_dir, cfg.paths.data_dir, names)
        print(json.dumps(info, ensure_ascii=False, sort_keys=True))
        return 0
    except (StateError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
