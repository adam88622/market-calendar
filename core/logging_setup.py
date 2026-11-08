"""FN-007 setup_logging：run-YYYYMMDD.log + (tty 時) stderr，清舊檔、截斷 launchd 日誌。"""
from __future__ import annotations

import logging
import re
import sys
from datetime import date, timedelta
from pathlib import Path

LOGGER_NAME = "market_calendar"
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
LAUNCHD_LOG_MAX_BYTES = 1024 * 1024
LAUNCHD_LOG_NAMES = ("launchd.out.log", "launchd.err.log")
_RUN_LOG_RE = re.compile(r"^run-(\d{8})\.log$")
_HANDLER_TAG = "_mci_handler"


def setup_logging(
    logs_dir: Path,
    today: date,
    level: str = "INFO",
    retention_days: int = 30,
) -> logging.Logger:
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.propagate = False

    # 重複呼叫：先移除本函式先前掛上的 handler
    for h in list(logger.handlers):
        if getattr(h, _HANDLER_TAG, False):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    fmt = logging.Formatter(LOG_FORMAT)
    fh = logging.FileHandler(
        logs_dir / f"run-{today.strftime('%Y%m%d')}.log", mode="a", encoding="utf-8"
    )
    fh.setFormatter(fmt)
    setattr(fh, _HANDLER_TAG, True)
    logger.addHandler(fh)

    stderr = sys.stderr
    try:
        is_tty = bool(stderr is not None and stderr.isatty())
    except Exception:
        is_tty = False
    if is_tty:
        sh = logging.StreamHandler(stderr)
        sh.setFormatter(fmt)
        setattr(sh, _HANDLER_TAG, True)
        logger.addHandler(sh)

    _purge_old_logs(logs_dir, today, retention_days, logger)
    _truncate_launchd_logs(logs_dir, logger)
    return logger


def _purge_old_logs(logs_dir: Path, today: date, retention_days: int, logger: logging.Logger) -> None:
    cutoff = today - timedelta(days=retention_days)
    for p in logs_dir.glob("run-*.log"):
        m = _RUN_LOG_RE.match(p.name)
        if not m:
            continue
        try:
            d = date(int(m.group(1)[:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8]))
        except ValueError:
            continue
        if d < cutoff:
            try:
                p.unlink()
            except OSError as e:
                logger.warning("failed to delete old log %s: %s", p, e)


def _truncate_launchd_logs(logs_dir: Path, logger: logging.Logger) -> None:
    for name in LAUNCHD_LOG_NAMES:
        p = logs_dir / name
        try:
            if p.is_file() and p.stat().st_size > LAUNCHD_LOG_MAX_BYTES:
                with open(p, "r+b") as f:  # 就地截斷（launchd 持有 fd 時仍有效）
                    f.truncate(0)
                logger.info("truncated %s (> 1MB)", name)
        except OSError as e:
            logger.warning("failed to truncate %s: %s", p, e)
