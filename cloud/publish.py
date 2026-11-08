"""FN-076（R18 最小版）：guard → build_status → 原子寫出 site/status.json 與 site/index.html。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from core.config import ConfigError, load_config
from core.models import CAL_ORDER
from cloud.status import (build_status, guard_outputs, inspect_ics, render_index_html)
from writer.atomic import write_atomic


def _read_json(path: Path | None):
    if path is None:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def publish_site(config_path: Path, report_path: Path | None, prev_dir: Path, site_url: str,
                 exit_code: int | None, *, restore_info_path: Path | None = None,
                 now_utc: datetime | None = None) -> dict:
    config_path = Path(config_path)
    cfg = load_config(config_path, config_path.resolve().parent)
    site_dir = Path(cfg.paths.output_dir)
    now_utc = now_utc or datetime.now(timezone.utc)
    calendars = [(c, cfg.calendars[c].name, cfg.calendars[c].filename) for c in CAL_ORDER]
    filenames = [fn for _, _, fn in calendars]

    report = _read_json(report_path)
    if not isinstance(report, dict):
        report = None
    restore_info = _read_json(restore_info_path)
    if not isinstance(restore_info, dict):
        restore_info = None

    guarded = guard_outputs(Path(prev_dir), site_dir, filenames)
    previous = _read_json(Path(prev_dir) / "status.json")
    files = {fn: inspect_ics(site_dir / fn) for fn in filenames}

    status = build_status(report=report, previous=previous, calendars=calendars, files=files,
                          site_url=site_url, now_utc=now_utc, guarded=frozenset(guarded),
                          exit_code=exit_code, restore_info=restore_info)
    write_atomic(site_dir / "status.json",
                 json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    write_atomic(site_dir / "index.html", render_index_html(status).encode("utf-8"))
    return status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cloud.publish")
    ap.add_argument("--config", required=True)
    ap.add_argument("--report")
    ap.add_argument("--prev-dir", required=True)
    ap.add_argument("--restore-info")
    ap.add_argument("--site-url", default="")
    ap.add_argument("--exit-code", default="")
    args = ap.parse_args(argv)
    try:
        code = int(args.exit_code) if str(args.exit_code).strip() != "" else None
        publish_site(Path(args.config), Path(args.report) if args.report else None,
                     Path(args.prev_dir), args.site_url, code,
                     restore_info_path=Path(args.restore_info) if args.restore_info else None)
        return 0
    except (ConfigError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
