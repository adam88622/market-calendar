"""V6-12 第 2 點：離線雲端端到端（真的 cloud.* 函式 + mock 網路）。

每個「runner」有自己的工作目錄（site / cloud-data / prev-site / 報告），
state 以 payload 目錄傳遞（等同 state 分支：save_state 輸出 → 下一個 runner 的 restore_state 輸入）。
"""
from __future__ import annotations

import functools
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import main
import pipelines
from cloud.publish import publish_site
from cloud.state import calendar_files, restore_state, save_state
from core.config import load_config
from tests.test_integration_run import (  # noqa: F401  (fixtures)
    TODAY, _cleanup_logging, mops_records, net, vevents,
)

T1 = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
T2 = T1 + timedelta(hours=3)
T3 = T1 + timedelta(hours=6)
T4 = T1 + timedelta(hours=9)
SITE_URL = "https://example.github.io/market-calendar/"
IDS = ["tw-settlement", "us-settlement", "tw-earnings", "us-earnings", "global-holidays"]
FILES = [f"{c}.ics" for c in IDS]


def make_config(prj_root: Path, runner_dir: Path) -> Path:
    """以 config.cloud.yaml 為底，只把 paths 三項改為 runner 目錄下的絕對路徑。"""
    text = (prj_root / "config.cloud.yaml").read_text(encoding="utf-8")
    assert "data_dir: cloud-data" in text  # 規格：雲端 data_dir 是 cloud-data
    text = re.sub(r"output_dir: site\b", f"output_dir: {runner_dir / 'site'}", text)
    text = re.sub(r"data_dir: cloud-data\b", f"data_dir: {runner_dir / 'cloud-data'}", text)
    text = re.sub(r"logs_dir: cloud-logs\b", f"logs_dir: {runner_dir / 'cloud-logs'}", text)
    runner_dir.mkdir(parents=True, exist_ok=True)
    p = runner_dir / "config.cloud.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def all_producers(net, overrides=None):
    p = {
        "tw-settlement": pipelines.produce_tw_settlement,
        "us-settlement": pipelines.produce_us_settlement,
        "tw-earnings": pipelines.produce_tw_earnings,
        "us-earnings": functools.partial(
            pipelines.produce_us_earnings, fetcher=net["us"], sleep=lambda s: None),
        "global-holidays": pipelines.produce_global_holidays,
    }
    p.update(overrides or {})
    return p


class Runner:
    """一個全新 runner 的一次完整流程：restore → main.run → publish_site → save_state。"""

    def __init__(self, prj_root: Path, base: Path, name: str):
        self.dir = base / name
        self.cfg_path = make_config(prj_root, self.dir)
        self.cfg = load_config(self.cfg_path, prj_root)
        self.site = Path(self.cfg.paths.output_dir)
        self.data = Path(self.cfg.paths.data_dir)
        self.prev = self.dir / "prev-site"
        self.report = self.dir / "run-report.json"
        self.payload = self.dir / "state-payload"
        assert self.site.name == "site" and self.data.name == "cloud-data"

    def restore(self, state_dir: Path | None, *, with_db: bool = True) -> dict:
        src = Path(state_dir) if state_dir else self.dir / "state-in"
        src.mkdir(parents=True, exist_ok=True)
        if not with_db:  # 對照組：只還原 site，不還原 DB
            tmp = self.dir / "state-nodb"
            shutil.copytree(src, tmp)
            shutil.rmtree(tmp / "data", ignore_errors=True)
            src = tmp
        return restore_state(src, self.site, self.data, self.prev, calendar_files(self.cfg))

    def run(self, net, now, overrides=None) -> tuple[int, dict]:
        code = main.run(self.cfg_path, today=TODAY, now_utc=now,
                        producers=all_producers(net, overrides), report_path=self.report)
        info = self.dir / "restore-info.json"
        status = publish_site(self.cfg_path, self.report, self.prev, SITE_URL, code,
                              restore_info_path=info if info.exists() else None, now_utc=now)
        return code, status

    def save(self) -> Path:
        save_state(self.payload, self.site, self.data, calendar_files(self.cfg))
        return self.payload

    def ics(self) -> dict[str, bytes]:
        return {c: (self.site / f"{c}.ics").read_bytes() for c in IDS}


def uid_lists(ics: dict[str, bytes]) -> dict[str, list[str]]:
    out = {}
    for c, b in ics.items():
        text = b.decode("utf-8").replace("\r\n ", "")
        out[c] = re.findall(r"^UID:(\S+)", text, re.M)
    return out


def dtstamps(b: bytes) -> dict[str, str]:
    return {u: re.search(r"DTSTAMP:(\S+)", blk).group(1) for u, blk in vevents(b).items()}


def by_id(status: dict) -> dict:
    return {c["id"]: c for c in status["calendars"]}


@pytest.fixture
def flow(tmp_path, prj_root, net):
    """Run1 → Run2（新 runner、還原 state）；回傳可再往下接 Run3/Run4 的上下文。"""
    r1 = Runner(prj_root, tmp_path, "r1")
    info1 = r1.restore(None)
    assert info1["db"] == "fresh" and info1["ics_restored"] == []
    code1, st1 = r1.run(net, T1)
    p1 = r1.save()
    r2 = Runner(prj_root, tmp_path, "r2")
    info2 = r2.restore(p1)
    code2, st2 = r2.run(net, T2)
    p2 = r2.save()
    return dict(tmp=tmp_path, prj=prj_root, net=net, r1=r1, r2=r2, st1=st1, st2=st2,
                code1=code1, code2=code2, info2=info2, p1=p1, p2=p2)


def test_run1_fresh_outputs(flow):
    r1, st1 = flow["r1"], flow["st1"]
    assert flow["code1"] == 0
    for fn in FILES + ["index.html", "status.json"]:
        assert (r1.site / fn).is_file(), fn
    assert all(c["last_attempt_status"] == "ok" and c["events"] > 0 for c in st1["calendars"])
    assert len(st1["calendars"]) == 5
    assert st1["run"]["exit_code"] == 0 and st1["run"]["state_db"] is None  # info 檔未傳入
    disk = json.loads((r1.site / "status.json").read_text(encoding="utf-8"))
    assert disk == st1
    for c in st1["calendars"]:
        assert c["url"] == SITE_URL + c["filename"]
        assert c["webcal_url"] == "webcal://" + (SITE_URL + c["filename"])[len("https://"):]
    html = (r1.site / "index.html").read_text(encoding="utf-8")
    assert all(c["url"] in html for c in st1["calendars"])
    # state payload 內容
    assert (flow["p1"] / "data" / "calendar.db").is_file()
    assert (flow["p1"] / "README.md").is_file()
    assert (flow["p1"] / "site" / "status.json").is_file()
    assert all((flow["p1"] / "site" / fn).is_file() for fn in FILES)
    assert (flow["p1"] / "site" / "index.html").is_file()


def test_run2_new_runner_bytes_identical(flow):
    r1, r2 = flow["r1"], flow["r2"]
    assert flow["code2"] == 0
    assert flow["info2"]["db"] == "restored"
    assert sorted(flow["info2"]["ics_restored"]) == sorted(FILES)
    assert flow["info2"]["status_restored"] is True
    assert r1.dir != r2.dir and r1.site != r2.site
    assert r2.ics() == r1.ics()  # 5 個 ics 位元組完全相同
    u1, u2 = uid_lists(r1.ics()), uid_lists(r2.ics())
    assert {k: set(v) for k, v in u1.items()} == {k: set(v) for k, v in u2.items()}
    for c, uids in u2.items():
        assert uids and len(uids) == len(set(uids)), c  # 同檔無重複 UID
    # status：last_success 由 Run2 更新、events 相同
    s1, s2 = by_id(flow["st1"]), by_id(flow["st2"])
    for c in IDS:
        assert s2[c]["events"] == s1[c]["events"] and s2[c]["sha256"] == s1[c]["sha256"]
        assert s2[c]["last_attempt_status"] == "ok"
        assert s2[c]["last_success_utc"] > s1[c]["last_success_utc"]


def test_run3_failed_producer_keeps_previous(flow):
    tmp, prj, net = flow["tmp"], flow["prj"], flow["net"]
    run2_ics, st2 = flow["r2"].ics(), by_id(flow["st2"])

    def boom(ctx):
        raise RuntimeError("nyse down")

    r3 = Runner(prj, tmp, "r3")
    r3.restore(flow["p2"])
    code, st3 = r3.run(net, T3, overrides={"us-settlement": boom})
    assert code == 1
    ics3 = r3.ics()
    assert ics3["us-settlement"] == run2_ics["us-settlement"]
    d3 = by_id(st3)
    assert d3["us-settlement"]["last_attempt_status"] == "failed"
    assert d3["us-settlement"]["last_success_utc"] == st2["us-settlement"]["last_success_utc"]
    assert st3["run"]["exit_code"] == 1 and "us-settlement" in st3["run"]["failures"]
    html = (r3.site / "index.html").read_text(encoding="utf-8")
    assert "沿用上一版" in html
    # 其他行事曆照常成功並更新 last_success
    for c in IDS:
        if c != "us-settlement":
            assert d3[c]["last_attempt_status"] == "ok"
            assert d3[c]["last_success_utc"] > st2[c]["last_success_utc"]
            assert ics3[c] == run2_ics[c]
    # 失敗狀態也可正常保存（workflow 在 exit code 1 時仍保存 state）
    r3.save()


def test_run4_empty_producer_guarded(flow):
    tmp, prj, net = flow["tmp"], flow["prj"], flow["net"]
    run2_ics = flow["r2"].ics()
    r4 = Runner(prj, tmp, "r4")
    r4.restore(flow["p2"])
    code, st4 = r4.run(net, T4, overrides={"tw-settlement": lambda ctx: []})
    d4 = by_id(st4)
    assert d4["tw-settlement"]["last_attempt_status"] == "guarded"
    assert r4.ics()["tw-settlement"] == run2_ics["tw-settlement"]
    assert d4["tw-settlement"]["events"] == by_id(flow["st2"])["tw-settlement"]["events"] > 0
    assert d4["tw-settlement"]["last_success_utc"] == by_id(flow["st2"])["tw-settlement"]["last_success_utc"]
    assert code == 0  # main 本身視為成功；由 guard 復原
    assert "沿用上一版" in (r4.site / "index.html").read_text(encoding="utf-8")


def test_control_without_db_restore_changes_dtstamp(flow):
    """對照組：Run2 不還原 DB（只還原 site）→ 累積資料消失、DTSTAMP 改變，證明還原 DB 必要。"""
    tmp, prj, net = flow["tmp"], flow["prj"], flow["net"]
    r1 = flow["r1"]
    rc = Runner(prj, tmp, "rc")
    info = rc.restore(flow["p1"], with_db=False)
    assert info["db"] == "fresh" and sorted(info["ics_restored"]) == sorted(FILES)
    code, _ = rc.run(net, T2)
    assert code == 0
    ics_c = rc.ics()
    # 所有行事曆的 DTSTAMP 都存在 DB（first_seen/last_changed）；DB 未還原 → 全部被改成 T2
    for c in IDS:
        d1, dc = dtstamps(r1.ics()[c]), dtstamps(ics_c[c])
        assert d1.keys() == dc.keys(), c  # UID 仍相同（UID 確定性）
        assert set(d1.values()) == {"20261108T000000Z"}
        assert set(dc.values()) == {"20261108T030000Z"}, c
        assert ics_c[c] != r1.ics()[c], c
