import json
from datetime import datetime, timezone
from html.parser import HTMLParser

from cloud.status import (FileInfo, build_status, classify_source, guard_outputs,
                          inspect_ics, render_index_html)

NOW = datetime(2026, 11, 9, 0, 33, 0, tzinfo=timezone.utc)
FIN = "2026-11-09T00:32:40Z"
CALS = [("tw-settlement", "台股結算", "tw-settlement.ics"),
        ("us-settlement", "美股結算", "us-settlement.ics"),
        ("tw-earnings", "台股法說", "tw-earnings.ics"),
        ("us-earnings", "美股財報", "us-earnings.ics"),
        ("global-holidays", "海外休市", "global-holidays.ics")]
FI = FileInfo(3, 100, "ab")
FILES = {fn: FI for _, _, fn in CALS}
SITE = "https://Example.github.io/market"


def report(statuses=None, sources=None, outcome="completed"):
    st = {c: "ok" for c, _, _ in CALS}
    st.update(statuses or {})
    return {
        "outcome": outcome, "exit_code": 0, "finished_at_utc": FIN, "failures": [],
        "calendars": {c: {"status": s, "events": 3, "error": None} for c, s in st.items()},
        "sources": sources if sources is not None else {
            "mops": {"records": 5, "errors": 0, "ok_scopes": 4, "expected_scopes": 4, "error_samples": []},
            "yahoo": {"enabled": True, "records": 3, "errors": 0, "error_samples": []},
            "yfinance": {"total": 57, "ok": 57, "timeout": 0, "queued": 0, "failed": 0},
        },
    }


def bs(rep, prev=None, **kw):
    return build_status(report=rep, previous=prev, calendars=CALS, files=FILES,
                        site_url=SITE, now_utc=NOW, **kw)


def test_classify_truth_table():
    c = classify_source
    assert c("mops", {"errors": 0, "ok_scopes": 4, "expected_scopes": 4}) == "ok"
    assert c("mops", {"errors": 1, "ok_scopes": 3, "expected_scopes": 4}) == "partial"
    assert c("mops", {"errors": 4, "ok_scopes": 0, "expected_scopes": 4}) == "failed"
    assert c("yahoo", {"enabled": False, "errors": 3}) == "disabled"
    assert c("yahoo", {"enabled": True, "errors": 0, "records": 0}) == "ok"
    assert c("yahoo", {"enabled": True, "errors": 1, "records": 2}) == "partial"
    assert c("yahoo", {"enabled": True, "errors": 1, "records": 0}) == "failed"
    assert c("yfinance", {"total": 0, "ok": 0}) == "disabled"
    assert c("yfinance", {"total": 5, "ok": 5}) == "ok"
    assert c("yfinance", {"total": 5, "ok": 2}) == "partial"
    assert c("yfinance", {"total": 5, "ok": 0}) == "failed"
    assert c("mops", None) == "unknown"


def test_first_run_ok():
    s = bs(report())
    assert all(c["last_attempt_status"] == "ok" and c["last_success_utc"] == FIN for c in s["calendars"])
    assert s["calendars"][0]["last_success_taipei"] == "2026-11-09 08:32"
    assert s["calendars"][0]["url"] == "https://example.github.io/market/tw-settlement.ics"
    assert s["calendars"][0]["webcal_url"] == "webcal://example.github.io/market/tw-settlement.ics"
    assert s["site_url"] == "https://example.github.io/market/"
    assert s["sources"]["mops"]["status"] == "ok"
    for k in ("health", "stale", "consecutive_failures"):
        assert k not in json.dumps(s)
    json.dumps(s)


def test_failed_keeps_previous_success():
    prev = bs(report())
    prev["calendars"][1]["last_success_utc"] = "2026-11-01T00:00:00Z"
    s = bs(report({"us-settlement": "failed"}), prev)
    c = s["calendars"][1]
    assert c["last_attempt_status"] == "failed" and c["last_success_utc"] == "2026-11-01T00:00:00Z"
    assert s["calendars"][0]["last_success_utc"] == FIN


def test_guarded():
    prev = bs(report())
    prev["calendars"][2]["last_success_utc"] = "2026-11-01T00:00:00Z"
    s = bs(report(), prev, guarded=frozenset({"tw-earnings.ics"}))
    c = s["calendars"][2]
    assert c["last_attempt_status"] == "guarded" and c["last_success_utc"] == "2026-11-01T00:00:00Z"


def test_source_failed_and_error_samples():
    src = {"mops": {"records": 0, "errors": 4, "ok_scopes": 0, "expected_scopes": 4, "error_samples": ["HTTP 403"]},
           "yahoo": {"enabled": False}, "yfinance": {"total": 0}}
    s = bs(report(sources=src))
    assert s["sources"]["mops"]["status"] == "failed"
    assert s["sources"]["mops"]["error_samples"] == ["HTTP 403"]
    assert s["sources"]["yahoo"]["status"] == "disabled"


def test_no_report_unknown():
    prev = bs(report())
    s = bs(None, prev)
    assert s["run"]["report_missing"] is True
    assert all(c["last_attempt_status"] == "unknown" for c in s["calendars"])
    assert all(c["last_success_utc"] == FIN for c in s["calendars"])
    assert all(v["status"] == "unknown" for v in s["sources"].values())


def test_invalid_previous():
    s = bs(report(), {"calendars": "bad"})
    assert s["run"]["previous_status_invalid"] is True


def test_site_url_empty():
    s = build_status(report=report(), previous=None, calendars=CALS, files=FILES,
                     site_url="", now_utc=NOW)
    assert s["calendars"][0]["url"] == "tw-settlement.ics" and s["calendars"][0]["webcal_url"] is None


class P(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs, self.text, self.tags = [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.hrefs += [v for k, v in attrs if k == "href"]

    def handle_data(self, d):
        self.text.append(d)


def test_render():
    s = bs(report())
    out = render_index_html(s)
    assert out == render_index_html(s)
    p = P()
    p.feed(out)
    assert sum(h.startswith("webcal://") for h in p.hrefs) == 5
    assert out.count("https://example.github.io/market/") == 5
    assert "最後成功更新" in out and "正常" in out
    assert "script" not in p.tags and "<script" not in out
    assert "被擋" not in out
    assert "MOPS（公開資訊觀測站）" in out


def test_render_escape_and_mops_hint():
    src = {"mops": {"records": 0, "errors": 4, "ok_scopes": 0, "expected_scopes": 4,
                    "error_samples": ["<i>x</i>"]},
           "yahoo": {"enabled": True, "errors": 0}, "yfinance": {"total": 1, "ok": 1}}
    cals = [("tw-settlement", "<b>x</b>", "tw-settlement.ics")]
    s = build_status(report=report({"tw-settlement": "failed"}, src), previous=None, calendars=cals,
                     files={"tw-settlement.ics": None}, site_url=SITE, now_utc=NOW)
    out = render_index_html(s)
    assert "<b>x</b>" not in out and "&lt;b&gt;x&lt;/b&gt;" in out
    assert "<i>x</i>" not in out
    assert "被擋海外 IP" in out
    assert "本次失敗（沿用上一版）" in out or "尚未產生" in out


def vc(n):
    return ("BEGIN:VCALENDAR\r\n" + "BEGIN:VEVENT\r\nEND:VEVENT\r\n" * n + "END:VCALENDAR\r\n").encode()


def test_inspect_and_guard(tmp_path):
    prev, site = tmp_path / "prev", tmp_path / "site"
    prev.mkdir(); site.mkdir()
    assert inspect_ics(site / "x.ics") is None
    (prev / "a.ics").write_bytes(vc(5)); (site / "a.ics").write_bytes(vc(0))
    (prev / "b.ics").write_bytes(vc(5)); (site / "b.ics").write_bytes(vc(3))
    (site / "c.ics").write_bytes(vc(0))
    (prev / "d.ics").write_bytes(vc(2))
    (prev / "e.ics").write_bytes(vc(0))
    assert inspect_ics(prev / "a.ics").events == 5
    r = guard_outputs(prev, site, ["a.ics", "b.ics", "c.ics", "d.ics", "e.ics", "f.ics"])
    assert r == ["a.ics", "d.ics"]
    assert (site / "a.ics").read_bytes() == vc(5)
    assert (site / "b.ics").read_bytes() == vc(3)
    assert (site / "c.ics").read_bytes() == vc(0)
    assert (site / "d.ics").read_bytes() == vc(2)
    assert not (site / "f.ics").exists()
