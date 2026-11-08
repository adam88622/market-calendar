"""FN-073（最小版）、FN-074、FN-075：status.json、index.html、空檔防護。"""
from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from writer.atomic import write_atomic

SCHEMA_VERSION = 1
_TAIPEI = ZoneInfo("Asia/Taipei")
_SOURCE_NAMES = {
    "mops": "MOPS（公開資訊觀測站）",
    "yahoo": "Yahoo 法說行事曆",
    "yfinance": "yfinance（美股財報）",
}
_SOURCE_ORDER = ("mops", "yahoo", "yfinance")
_STATUS_ZH = {
    "ok": "正常", "degraded": "部分降級", "failed": "本次失敗（沿用上一版）",
    "guarded": "空檔防護（沿用上一版）", "skipped": "本次未執行", "not_run": "本次未執行",
    "unknown": "未知",
}
_SRC_ZH = {"ok": "正常", "partial": "部分成功", "failed": "失敗", "disabled": "停用", "unknown": "未知"}


@dataclass(frozen=True)
class FileInfo:
    events: int
    bytes: int
    sha256: str


def inspect_ics(path: Path) -> FileInfo | None:
    path = Path(path)
    try:
        data = path.read_bytes()
    except (FileNotFoundError, IsADirectoryError):
        return None
    return FileInfo(data.count(b"BEGIN:VEVENT"), len(data), hashlib.sha256(data).hexdigest())


def classify_source(name: str, stats: dict | None) -> str:
    if not isinstance(stats, dict):
        return "unknown"
    g = lambda k: stats.get(k) or 0  # noqa: E731
    if name == "mops":
        if g("errors") == 0 and g("ok_scopes") == g("expected_scopes"):
            return "ok"
        return "partial" if g("ok_scopes") > 0 else "failed"
    if name == "yahoo":
        if stats.get("enabled") is False:
            return "disabled"
        if g("errors") == 0:
            return "ok"
        return "partial" if g("records") > 0 else "failed"
    if name == "yfinance":
        if g("total") == 0:
            return "disabled"
        if g("ok") == g("total"):
            return "ok"
        return "partial" if g("ok") > 0 else "failed"
    return "unknown"


def _norm_site_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    sp = urlsplit(url)
    path = sp.path if sp.path.endswith("/") else sp.path + "/"
    return urlunsplit((sp.scheme, sp.netloc.lower(), path, "", ""))


def _taipei(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.astimezone(_TAIPEI).strftime("%Y-%m-%d %H:%M")


def _fmt_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_previous(prev) -> bool:
    return (isinstance(prev, dict) and isinstance(prev.get("calendars"), list)
            and all(isinstance(c, dict) for c in prev["calendars"]))


def build_status(*, report: dict | None, previous: dict | None,
                 calendars: list[tuple[str, str, str]],
                 files: dict[str, FileInfo | None], site_url: str, now_utc: datetime,
                 guarded: frozenset[str] = frozenset(), exit_code: int | None = None,
                 restore_info: dict | None = None) -> dict:
    prev_invalid = previous is not None and not _valid_previous(previous)
    if prev_invalid:
        previous = None
    prev_cals = {c.get("id"): c for c in (previous or {}).get("calendars", [])}
    site_url = _norm_site_url(site_url)
    completed = isinstance(report, dict) and report.get("outcome") == "completed"
    rep_cals = (report or {}).get("calendars") or {}
    finished = (report or {}).get("finished_at_utc") if isinstance(report, dict) else None

    cal_out = []
    for cid, name, fn in calendars:
        status = "unknown"
        if isinstance(report, dict):
            status = (rep_cals.get(cid) or {}).get("status") or "not_run"
            if not completed:
                status = status if status in _STATUS_ZH else "not_run"
        if fn in guarded:
            status = "guarded"
        prev_c = prev_cals.get(cid) or {}
        last_utc = prev_c.get("last_success_utc")
        if status in ("ok", "degraded") and fn not in guarded and finished:
            last_utc = finished
        info = files.get(fn)
        url = site_url + fn if site_url else fn
        webcal = None
        if site_url:
            for pre in ("https://", "http://"):
                if site_url.startswith(pre):
                    webcal = "webcal://" + url[len(pre):]
                    break
        cal_out.append({
            "id": cid, "name": name, "filename": fn, "url": url, "webcal_url": webcal,
            "last_attempt_status": status,
            "file_present": info is not None,
            "events": info.events if info else None,
            "bytes": info.bytes if info else None,
            "sha256": info.sha256 if info else None,
            "last_success_utc": last_utc,
            "last_success_taipei": _taipei(last_utc),
        })

    srcs_raw = (report or {}).get("sources") if isinstance(report, dict) else None
    srcs_raw = srcs_raw if (completed and isinstance(srcs_raw, dict)) else {}
    sources = {}
    for n in _SOURCE_ORDER:
        st = srcs_raw.get(n)
        entry = {"status": classify_source(n, st) if completed else "unknown"}
        if isinstance(st, dict):
            entry["detail"] = {k: v for k, v in st.items() if k != "error_samples"}
            entry["error_samples"] = [str(x) for x in (st.get("error_samples") or [])]
        else:
            entry["detail"] = {}
            entry["error_samples"] = []
        sources[n] = entry

    run = {
        "outcome": report.get("outcome") if isinstance(report, dict) else None,
        "exit_code": exit_code if exit_code is not None else
        (report.get("exit_code") if isinstance(report, dict) else None),
        "failures": list(report.get("failures") or []) if isinstance(report, dict) else [],
        "state_db": (restore_info or {}).get("db"),
        "report_missing": report is None,
    }
    if prev_invalid:
        run["previous_status_invalid"] = True
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _fmt_utc(now_utc),
        "generated_at_taipei": now_utc.astimezone(_TAIPEI).strftime("%Y-%m-%d %H:%M")
        if now_utc.tzinfo else now_utc.replace(tzinfo=timezone.utc).astimezone(_TAIPEI).strftime("%Y-%m-%d %H:%M"),
        "site_url": site_url,
        "run": run,
        "calendars": cal_out,
        "sources": sources,
    }


def _e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _src_note(n: str, s: dict) -> str:
    d = s.get("detail") or {}
    if n == "mops" and "expected_scopes" in d:
        return f"{d.get('ok_scopes', 0)}/{d['expected_scopes']} 個查詢成功"
    if n == "yfinance" and "total" in d:
        return f"{d.get('ok', 0)}/{d['total']} 檔成功"
    if n == "yahoo" and "records" in d:
        return f"{d['records']} 筆資料"
    return ""


def render_index_html(status: dict) -> str:
    rows = []
    for c in status.get("calendars", []):
        link = (f'<a href="{_e(c["webcal_url"])}">Apple 一鍵訂閱</a>' if c.get("webcal_url") else "-")
        last = c.get("last_success_taipei") or "尚未產生"
        st = c.get("last_attempt_status")
        zh = "尚未產生" if not c.get("file_present") and st != "ok" else _STATUS_ZH.get(st, _e(st))
        ev = "-" if c.get("events") is None else c["events"]
        rows.append(
            f"<tr><td>{_e(c['name'])}</td><td><code>{_e(c['url'])}</code></td><td>{link}</td>"
            f"<td>{_e(last)}</td><td>{_e(zh)}</td><td>{_e(ev)}</td></tr>"
        )
    srows = []
    srcs = status.get("sources", {})
    for n in _SOURCE_ORDER:
        s = srcs.get(n)
        if not s:
            continue
        samples = "；".join(s.get("error_samples") or [])
        note = _src_note(n, s) + (f"（{samples}）" if samples else "")
        srows.append(
            f"<tr><td>{_e(_SOURCE_NAMES[n])}</td><td>{_e(_SRC_ZH.get(s.get('status'), s.get('status')))}</td>"
            f"<td>{_e(note)}</td></tr>"
        )
    hint = ""
    if (srcs.get("mops") or {}).get("status") == "failed":
        hint = "<p class=\"warn\">MOPS 本次查詢失敗，可能被擋海外 IP，已改用累積資料與 Yahoo。</p>"
    successes = [c.get("last_success_taipei") for c in status.get("calendars", []) if c.get("last_success_taipei")]
    latest = max(successes) if successes else "尚無"
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>市場行事曆訂閱</title>
<style>
body{{font-family:-apple-system,"Noto Sans TC",sans-serif;max-width:960px;margin:2em auto;padding:0 1em;color:#222}}
table{{border-collapse:collapse;width:100%;margin:1em 0}}
th,td{{border:1px solid #ccc;padding:6px 8px;text-align:left;font-size:14px}}
th{{background:#f3f3f3}}code{{word-break:break-all}}.warn{{color:#b45309}}
</style>
</head>
<body>
<h1>市場行事曆訂閱</h1>
<p>最後成功更新：{_e(latest)}（台北時間）；本頁產生時間：{_e(status.get('generated_at_taipei'))}</p>
<h2>行事曆</h2>
<table>
<tr><th>名稱</th><th>訂閱網址</th><th>Apple 一鍵訂閱</th><th>最後成功更新</th><th>本次狀態</th><th>事件數</th></tr>
{chr(10).join(rows)}
</table>
<h2>資料來源狀態</h2>
<table>
<tr><th>來源</th><th>本次狀態</th><th>說明</th></tr>
{chr(10).join(srows)}
</table>
{hint}
<p>資料僅供參考，以交易所與公司公告為準；行事曆內時間為台北時間。 <a href="status.json">status.json</a></p>
</body>
</html>
"""


def guard_outputs(prev_dir: Path, site_dir: Path, filenames: list[str]) -> list[str]:
    restored = []
    for fn in filenames:
        old = inspect_ics(Path(prev_dir) / fn)
        if old is None or old.events <= 0:
            continue
        new = inspect_ics(Path(site_dir) / fn)
        if new is None or new.events == 0:
            write_atomic(Path(site_dir) / fn, (Path(prev_dir) / fn).read_bytes())
            restored.append(fn)
    return restored
