"""FN-044 run() 整合測試（測試策略第 6 節；全部 mock 網路、離線）。"""
from __future__ import annotations

import functools
import logging
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import main
import pipelines
from core.models import FetchResult, UsEarningRecord
from scrapers.mops_ir import parse_mops_ir_html

FX = Path(__file__).resolve().parent / "fixtures"
TODAY = date(2026, 11, 8)
T1 = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
T2 = T1 + timedelta(hours=3)
T3 = T1 + timedelta(hours=6)
CALS = ["tw-settlement", "us-settlement", "tw-earnings", "us-earnings"]
BASE_TICKERS = ["NVDA", "AMD", "CRWV", "AAPL"]


@pytest.fixture(autouse=True)
def _cleanup_logging():
    yield
    lg = logging.getLogger("market_calendar")
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()


def write_config(tmp: Path, extra: list[str] = (), drop: tuple[str, ...] = (),
                 base: str = "config_test.yaml") -> Path:
    text = (FX / base).read_text(encoding="utf-8")
    text = (text.replace("out_test", str(tmp / "output"))
            .replace("data_test", str(tmp / "data"))
            .replace("logs_test", str(tmp / "logs")))
    lines = []
    for ln in text.splitlines():
        if any(f"ticker: {t}," in ln for t in drop):
            continue
        lines.append(ln)
        if "ticker: AAPL" in ln:
            lines.extend(extra)
    p = tmp / "config.yaml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def mops_records():
    html = (FX / "mops_t100sb02_1_sii_115_11.html").read_text(encoding="utf-8")
    recs = parse_mops_ir_html(html, "sii")
    assert recs
    recs[0] = replace(recs[0], location="地點A")
    return recs


@pytest.fixture
def net(monkeypatch, mops_records):
    """可調整的網路 mock 狀態。"""
    state = {"mops": mops_records, "fail": False, "extra_ticker": {}}

    def fake_mops(session, cfg, today, *a, **k):
        if state["fail"]:
            return FetchResult(records=[], errors=["mock mops down"])
        return FetchResult(records=list(state["mops"]))

    def fake_yahoo(session, cfg, *a, **k):
        if state["fail"]:
            return FetchResult(records=[], errors=["mock yahoo down"])
        return FetchResult(records=[])

    def fake_us(ticker, limit, today_et):
        if state["fail"]:
            raise RuntimeError("mock yfinance down")
        return [UsEarningRecord(ticker, date(2026, 11, 25), "16:00", "盤後", "確認")]

    monkeypatch.setattr(pipelines, "fetch_mops_ir", fake_mops)
    monkeypatch.setattr(pipelines, "fetch_yahoo_ir", fake_yahoo)
    state["us"] = fake_us
    return state


def producers(net, overrides=None):
    p = {
        "tw-settlement": pipelines.produce_tw_settlement,
        "us-settlement": pipelines.produce_us_settlement,
        "tw-earnings": pipelines.produce_tw_earnings,
        "us-earnings": functools.partial(
            pipelines.produce_us_earnings, fetcher=net["us"], sleep=lambda s: None),
    }
    p.update(overrides or {})
    return p


def go(cfg_path, net, now=T1, today=TODAY, overrides=None, apple=None):
    return main.run(cfg_path, today=today, now_utc=now, producers=producers(net, overrides),
                    apple_adapter=apple)


def outputs(tmp: Path) -> dict[str, bytes]:
    return {c: (tmp / "output" / f"{c}.ics").read_bytes() for c in CALS}


def unfold(b: bytes) -> str:
    return b.decode("utf-8").replace("\r\n ", "")


def vevents(b: bytes) -> dict[str, str]:
    out = {}
    for blk in re.findall(r"BEGIN:VEVENT\r\n.*?END:VEVENT", unfold(b), re.S):
        uid = re.search(r"UID:(\S+)", blk).group(1)
        out[uid] = blk
    return out


def test_idempotent_bytes(tmp_path, net):
    cfg = write_config(tmp_path)
    assert go(cfg, net, T1) == 0
    first = outputs(tmp_path)
    assert go(cfg, net, T2) == 0
    assert outputs(tmp_path) == first
    for b in first.values():
        assert b.startswith(b"BEGIN:VCALENDAR\r\n") and b"BEGIN:VEVENT" in b


def test_content_change_only_that_event(tmp_path, net, mops_records):
    cfg = write_config(tmp_path)
    assert go(cfg, net, T1) == 0
    before = vevents(outputs(tmp_path)["tw-earnings"])
    net["mops"] = [replace(mops_records[0], location="地點B")] + mops_records[1:]
    assert go(cfg, net, T3) == 0
    after = vevents(outputs(tmp_path)["tw-earnings"])
    assert before.keys() == after.keys()
    changed = [u for u in after if after[u] != before[u]]
    assert len(changed) == 1
    blk = after[changed[0]]
    assert "SEQUENCE:1" in blk and "DTSTAMP:20261108T060000Z" in blk and "地點B" in blk
    assert "SEQUENCE:0" in before[changed[0]]


def test_network_all_fail_still_outputs(tmp_path, net):
    cfg = write_config(tmp_path)
    assert go(cfg, net, T1) == 0
    first = outputs(tmp_path)
    net["fail"] = True
    assert go(cfg, net, T2) == 1  # BUG-1：0/4 成功 → us-earnings:degraded
    second = outputs(tmp_path)
    assert second["tw-earnings"] == first["tw-earnings"]  # 累積庫＋截止日
    assert second["us-earnings"] == first["us-earnings"]
    log = (tmp_path / "logs" / "run-20261108.log").read_text(encoding="utf-8")
    assert "WARNING" in log and "fetch failed" in log


def test_network_fail_on_fresh_run(tmp_path, net):
    cfg = write_config(tmp_path)
    net["fail"] = True
    assert go(cfg, net, T1) == 1  # BUG-1：us-earnings:degraded
    out = outputs(tmp_path)
    assert b"BEGIN:VEVENT" in out["tw-earnings"]  # 截止日
    assert b"BEGIN:VCALENDAR" in out["us-earnings"]


def test_single_calendar_failure(tmp_path, net):
    cfg = write_config(tmp_path)
    assert go(cfg, net, T1) == 0
    before = outputs(tmp_path)

    def boom(ctx):
        raise RuntimeError("nyse down")

    assert go(cfg, net, T2, overrides={"us-settlement": boom}) == 1
    after = outputs(tmp_path)
    assert after["us-settlement"] == before["us-settlement"]
    for c in ("tw-settlement", "tw-earnings", "us-earnings"):
        assert after[c] == before[c]
    log = (tmp_path / "logs" / "run-20261108.log").read_text(encoding="utf-8")
    assert "Traceback" in log and "us-settlement failed; previous file kept" in log
    assert not [p for p in (tmp_path / "output").iterdir() if ".tmp-" in p.name]


def test_watchlist_changes(tmp_path, net):
    cfg = write_config(tmp_path, extra=["    - {ticker: INTC, group: 其他}"])
    assert go(cfg, net, T1) == 0
    assert "INTC" in unfold(outputs(tmp_path)["us-earnings"])

    cfg = write_config(tmp_path)  # 移除 INTC
    assert go(cfg, net, T2) == 0
    assert "INTC" not in unfold(outputs(tmp_path)["us-earnings"])

    cfg = write_config(tmp_path, extra=["    - {ticker: PLTR, group: 其他}"])
    assert go(cfg, net, T3) == 0
    text = unfold(outputs(tmp_path)["us-earnings"])
    assert "PLTR" in text and "分組：其他" in text


def test_cross_month_range(tmp_path, net):
    cfg = write_config(tmp_path)
    seen = {}

    def spy(ctx):
        seen["r"] = (ctx.range_start, ctx.range_end)
        return pipelines.produce_tw_settlement(ctx)

    now = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)
    assert go(cfg, net, now, today=date(2026, 12, 1), overrides={"tw-settlement": spy}) == 0
    assert seen["r"] == (date(2026, 9, 1), date(2027, 12, 31))


def test_config_error_returns_2(tmp_path, net, monkeypatch):
    # config 載入失敗的 fallback 日誌預設寫 $PRJ/logs；測試時導向 tmp_path，避免污染正式日誌
    orig = main._config_failure
    monkeypatch.setattr(main, "_config_failure", lambda prj_root, today, err: orig(tmp_path, today, err))
    bad = tmp_path / "bad.yaml"
    bad.write_text("timezone: [", encoding="utf-8")
    assert main.run(bad, today=TODAY, now_utc=T1, producers=producers(net)) == 2
    assert not (tmp_path / "output").exists()
    log = (tmp_path / "logs" / "run-20261108.log").read_text(encoding="utf-8")
    assert "config error:" in log and "bad.yaml" in log


def test_hung_workers_forced_exit(tmp_path, net, monkeypatch):
    cfg = write_config(tmp_path)
    codes = []
    monkeypatch.setattr(main, "_force_exit", codes.append)
    rc = go(cfg, net, T1, overrides={"us-earnings": lambda ctx: ([], 2)})
    assert codes == [0] and rc == 0
    log = (tmp_path / "logs" / "run-20261108.log").read_text(encoding="utf-8")
    assert "2 yfinance workers still running at exit; forced exit" in log
    assert (tmp_path / "output" / "us-earnings.ics").exists()


# ---------------------------------------------------------------- FN-057（F-008）
APPLE_CFG = "config_test_apple.yaml"
NAMES = {"tw-settlement": "台股結算", "us-settlement": "美股結算",
         "tw-earnings": "台股法說與財報", "us-earnings": "美股財報（AI 供應鏈）"}


def fake_events(fake, cal_id):
    ident = next(c.identifier for c in fake.calendars.values() if c.title == NAMES[cal_id])
    return fake.events_of(ident)


def read_log(tmp: Path) -> str:
    return (tmp / "logs" / "run-20261108.log").read_text(encoding="utf-8")


def test_apple_sync_counts_match_ics(tmp_path, net):
    from tests.fake_calendar_adapter import FakeCalendarAdapter

    fake = FakeCalendarAdapter()
    cfg = write_config(tmp_path, base=APPLE_CFG)
    assert go(cfg, net, T1, apple=fake) == 0
    out = outputs(tmp_path)
    for c in CALS:
        assert len(fake_events(fake, c)) == len(vevents(out[c])) > 0
    assert "authorization status=fullAccess" in read_log(tmp_path)


def test_apple_second_run_no_ops(tmp_path, net):
    from tests.fake_calendar_adapter import FakeCalendarAdapter

    fake = FakeCalendarAdapter()
    cfg = write_config(tmp_path, base=APPLE_CFG)
    assert go(cfg, net, T1, apple=fake) == 0
    first = outputs(tmp_path)
    fake.ops.clear()
    assert go(cfg, net, T2, apple=fake) == 0
    assert outputs(tmp_path) == first
    assert not {"create", "update", "delete", "create_calendar", "rename_calendar"} & set(
        fake.op_names())


def test_apple_failed_producer_not_synced(tmp_path, net):
    from tests.fake_calendar_adapter import FakeCalendarAdapter

    fake = FakeCalendarAdapter()
    cfg = write_config(tmp_path, base=APPLE_CFG)
    assert go(cfg, net, T1, apple=fake) == 0
    before_ics = outputs(tmp_path)
    before_us = fake_events(fake, "us-settlement")
    assert before_us

    def boom(ctx):
        raise RuntimeError("nyse down")

    fake.ops.clear()
    assert go(cfg, net, T2, overrides={"us-settlement": boom}, apple=fake) == 1
    assert outputs(tmp_path)["us-settlement"] == before_ics["us-settlement"]
    assert fake_events(fake, "us-settlement") == before_us
    us_id = next(c.identifier for c in fake.calendars.values() if c.title == NAMES["us-settlement"])
    assert not any(us_id in str(o) for o in fake.ops)
    assert sum(1 for o in fake.ops if o[0] == "commit") == 3
    assert "apple us-settlement: not produced this run; skipped" in read_log(tmp_path)


def test_apple_denied(tmp_path, net):
    from tests.fake_calendar_adapter import FakeCalendarAdapter

    fake = FakeCalendarAdapter(status="denied")
    cfg = write_config(tmp_path, base=APPLE_CFG)
    assert go(cfg, net, T1, apple=fake) == 1
    out = outputs(tmp_path)
    assert all(b.startswith(b"BEGIN:VCALENDAR\r\n") and b"BEGIN:VEVENT" in b for b in out.values())
    log = read_log(tmp_path)
    assert any("ERROR" in ln and "--authorize" in ln for ln in log.splitlines())
    assert not {"create", "commit", "create_calendar"} & set(fake.op_names())


def test_apple_disabled_never_touches_adapter(tmp_path, net):
    class Explode:
        def __getattr__(self, name):
            raise AssertionError(f"adapter touched: {name}")

    cfg = write_config(tmp_path)  # config_test.yaml：沒有 apple_calendar 區段
    assert go(cfg, net, T1, apple=Explode()) == 0
    assert "apple calendar: disabled" in read_log(tmp_path)


def test_main_authorize_passthrough(monkeypatch):
    import apple.authorize

    monkeypatch.setattr(apple.authorize, "authorize", lambda *a, **k: 7)
    called = []
    monkeypatch.setattr(main, "run", lambda *a, **k: called.append(a) or 0)
    assert main.main(["--authorize"]) == 7
    assert called == []


def test_main_config_arg(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(main, "run", lambda p=None, **k: seen.append(p) or 0)
    assert main.main([]) == 0
    assert main.main(["--config", str(tmp_path / "c.yaml")]) == 0
    assert seen == [None, tmp_path / "c.yaml"]


# ---------------------------------------------------------------- BUG-1：降級標示
EXTRA6 = [f"    - {{ticker: X{i}, group: 測試}}" for i in range(6)]  # 共 10 檔


def _failing_us(net, bad):
    def f(ticker, limit, today_et):
        if ticker in bad:
            raise RuntimeError("mock fail")
        return net["us"](ticker, limit, today_et)
    return f


def _us_override(fetcher):
    return {"us-earnings": functools.partial(
        pipelines.produce_us_earnings, fetcher=fetcher, sleep=lambda s: None)}


def test_degraded_70_percent_exit_1_ics_still_written(tmp_path, net):
    cfg = write_config(tmp_path, extra=EXTRA6)
    bad = {"X0", "X1", "X2"}
    assert go(cfg, net, T1, overrides=_us_override(_failing_us(net, bad))) == 1
    assert (tmp_path / "output" / "us-earnings.ics").exists()
    log = read_log(tmp_path)
    assert "us-earnings degraded: 7/10 fetched; missing tickers keep cached data" in log
    assert "us-earnings:degraded" in log


def test_not_degraded_90_percent_exit_0(tmp_path, net):
    cfg = write_config(tmp_path, extra=EXTRA6)
    assert go(cfg, net, T1, overrides=_us_override(_failing_us(net, {"X0"}))) == 0
    assert "us-earnings degraded" not in read_log(tmp_path)
    assert "failures=none" in read_log(tmp_path)


def test_degraded_f008_does_not_delete_unfetched_future_events(tmp_path, net):
    from tests.fake_calendar_adapter import FakeCalendarAdapter

    fake = FakeCalendarAdapter()
    cfg = write_config(tmp_path, base=APPLE_CFG, extra=EXTRA6)
    assert go(cfg, net, T1, apple=fake) == 0
    before = fake_events(fake, "us-earnings")
    assert any("X0" in str(e) for e in before)
    fake.ops.clear()
    bad = {"X0", "X1", "X2"}
    assert go(cfg, net, T2, overrides=_us_override(_failing_us(net, bad)), apple=fake) == 1
    assert "delete" not in fake.op_names()
    assert fake_events(fake, "us-earnings") == before
    assert any("X0" in str(e) for e in fake_events(fake, "us-earnings"))


def test_legacy_producer_returns_still_supported(tmp_path, net):
    cfg = write_config(tmp_path)
    assert go(cfg, net, T1, overrides={"us-earnings": lambda ctx: ([], 0, False)}) == 0
    assert go(cfg, net, T2, overrides={"us-earnings": lambda ctx: ([], 0, True)}) == 1


# ---- 離線跳過
def _snapshot(tmp):
    return {str(p.relative_to(tmp)): p.read_bytes()
            for sub in ("output", "data") for p in (tmp / sub).rglob("*") if p.is_file()}


def test_offline_skips_everything(tmp_path, net):
    cfg = write_config(tmp_path)
    assert go(cfg, net, T1) == 0
    before = _snapshot(tmp_path)
    called = []

    def spy(ctx):
        called.append(1)
        raise AssertionError("producer must not run when offline")

    ov = {c: spy for c in CALS}
    rc = main.run(cfg, today=TODAY, now_utc=T2, producers=producers(net, ov),
                  online_check=lambda: False)
    assert rc == 0 and called == []
    assert _snapshot(tmp_path) == before
    log = (tmp_path / "logs" / "run-20261108.log").read_text(encoding="utf-8")
    assert "offline: network check failed; skipped this run (no files or calendars changed)" in log


def test_offline_fresh_creates_nothing(tmp_path, net):
    cfg = write_config(tmp_path)
    rc = main.run(cfg, today=TODAY, now_utc=T1, producers=producers(net),
                  online_check=lambda: False)
    assert rc == 0
    assert not (tmp_path / "output").exists() and not (tmp_path / "data").exists()


def test_online_runs_normally(tmp_path, net):
    cfg = write_config(tmp_path)
    rc = main.run(cfg, today=TODAY, now_utc=T1, producers=producers(net),
                  online_check=lambda: True)
    assert rc == 0 and set(outputs(tmp_path)) == set(CALS)


def test_online_check_exception_treated_offline(tmp_path, net):
    cfg = write_config(tmp_path)

    def boom():
        raise RuntimeError("x")

    assert main.run(cfg, today=TODAY, now_utc=T1, producers=producers(net),
                    online_check=boom) == 0
    assert not (tmp_path / "output").exists()


def test_config_network_check_enabled_uses_is_online(tmp_path, net, monkeypatch):
    cfg = write_config(tmp_path)
    cfg.write_text(cfg.read_text(encoding="utf-8")
                   + "network_check: {enabled: true, timeout_sec: 2}\n", encoding="utf-8")
    seen = {}

    def fake(urls, timeout_sec=4.0, session=None):
        seen["a"] = (urls, timeout_sec)
        return False

    monkeypatch.setattr("core.netcheck.is_online", fake)
    assert main.run(cfg, today=TODAY, now_utc=T1, producers=producers(net)) == 0
    assert seen["a"][1] == 2.0 and len(seen["a"][0]) == 2
    assert not (tmp_path / "output").exists()
