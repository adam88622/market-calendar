"""V6-12 第 4 點：從 dist 的乾淨複本 + workflow 本機模擬（需要網路、uv、git；耗時數分鐘）。

流程：export_kit → 解壓 A → bare state.git → run_workflow_locally(build + save-state)
→ 解壓 B（全新 runner）以同一個 remote 再跑一次 → 驗證穩定性 → HTTP 伺服 B/site 驗證訂閱網址。

環境變數 MCI_LIVE_WORKDIR（可選）：指定工作目錄並保留，供事後檢查（預設用 pytest tmp_path）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import sys
import threading
import urllib.request
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

PRJ = Path(__file__).resolve().parent.parent
IDS = ["tw-settlement", "us-settlement", "tw-earnings", "us-earnings", "global-holidays"]
RULE_BASED = ["tw-settlement", "us-settlement", "global-holidays"]
ROOT = "market-calendar-kit"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _unzip(zip_path: Path, dest: Path) -> Path:
    """解壓並還原 unix 權限；回傳 kit 根目錄。"""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for zi in zf.infolist():
            target = dest / zi.filename
            if zi.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(zi))
            mode = (zi.external_attr >> 16) & 0o777
            if mode:
                target.chmod(mode)
    return dest / ROOT


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _unfold(b: bytes) -> str:
    return b.decode("utf-8").replace("\r\n ", "")


def _events(b: bytes) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for blk in re.findall(r"BEGIN:VEVENT\r\n.*?END:VEVENT", _unfold(b), re.S):
        out.setdefault(re.search(r"^UID:(\S+)", blk, re.M).group(1), []).append(blk)
    return out


def _uids(b: bytes) -> list[str]:
    return re.findall(r"^UID:(\S+)", _unfold(b), re.M)


def _content_hash(blk: str) -> str:
    body = "\n".join(ln for ln in blk.splitlines()
                     if not ln.startswith(("DTSTAMP:", "SEQUENCE:", "LAST-MODIFIED:")))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _field(blk: str, name: str) -> str:
    m = re.search(rf"^{name}:(\S+)", blk, re.M)
    return m.group(1) if m else ""


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    try:
        from tools import export_kit as ek
    except ImportError:
        pytest.skip("找不到 tools/（安裝包內不含）")
    sys.path.insert(0, str(PRJ / "tests"))
    try:
        from workflow_sim import run_workflow_locally
    except ImportError:
        pytest.skip("找不到 tests/workflow_sim.py")
    if subprocess.run(["which", "uv"], capture_output=True).returncode != 0:
        pytest.skip("找不到 uv")

    keep = os.environ.get("MCI_LIVE_WORKDIR")
    base = Path(keep) if keep else tmp_path_factory.mktemp("cloud_live")
    base.mkdir(parents=True, exist_ok=True)

    # 1. export → 解壓 A
    res = ek.export_kit(PRJ, base / "dist")
    kit_a = _unzip(res.zip_path, base / "A_root")
    for p in kit_a.rglob("*"):
        if p.is_file() and p.suffix in (".command", ".sh"):
            assert stat.S_IMODE(p.stat().st_mode) == 0o755, p
    assert (kit_a / ".github" / "workflows" / "publish.yml").is_file()

    # 2. bare state.git
    state_git = base / "state.git"
    r = _git("init", "--bare", "--quiet", "--initial-branch=state", str(state_git))
    assert r.returncode == 0, r.stderr
    remote = f"file://{state_git}"
    ls = _git("ls-remote", "--exit-code", "--heads", remote, "state")
    port = _free_port()
    site_url = f"http://127.0.0.1:{port}/"

    def env(run_id: str) -> dict:
        return {"MCI_STATE_REMOTE": remote, "GH_TOKEN": "dummy", "MCI_SITE_URL": site_url,
                "GITHUB_REPOSITORY": "local/kit", "GITHUB_REPOSITORY_OWNER": "local",
                "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": run_id}

    # 3. 第一次（A）
    wf_a = kit_a / ".github" / "workflows" / "publish.yml"
    res_a = run_workflow_locally(wf_a, kit_a, jobs=("build", "save-state"),
                                 env_overrides=env("1"))
    commits_a = _git("--git-dir", str(state_git), "rev-list", "--count", "state")
    snap_a = {c: (kit_a / "site" / f"{c}.ics").read_bytes() for c in IDS}
    status_a = json.loads((kit_a / "site" / "status.json").read_text(encoding="utf-8"))

    # 4. 第二次（B，全新 runner）
    kit_b = _unzip(res.zip_path, base / "B_root")
    wf_b = kit_b / ".github" / "workflows" / "publish.yml"
    res_b = run_workflow_locally(wf_b, kit_b, jobs=("build", "save-state"),
                                 env_overrides=env("2"))
    commits_b = _git("--git-dir", str(state_git), "rev-list", "--count", "state")
    snap_b = {c: (kit_b / "site" / f"{c}.ics").read_bytes() for c in IDS}
    status_b = json.loads((kit_b / "site" / "status.json").read_text(encoding="utf-8"))

    yield dict(base=base, kit_a=kit_a, kit_b=kit_b, state_git=state_git, remote=remote,
               ls=ls, port=port, site_url=site_url, res_a=res_a, res_b=res_b,
               commits_a=commits_a, commits_b=commits_b, snap_a=snap_a, snap_b=snap_b,
               status_a=status_a, status_b=status_b)


def test_empty_bare_ls_remote_exit_code(live):
    """空的 bare repo + file:// 的 ls-remote --exit-code：須能與「讀取失敗」區分（workflow 預期 2）。"""
    assert live["ls"].returncode == 2, (live["ls"].returncode, live["ls"].stderr)


def test_first_run_outputs_and_state(live):
    a = live["kit_a"]
    for fn in [f"{c}.ics" for c in IDS] + ["index.html", "status.json"]:
        assert (a / "site" / fn).is_file(), fn
    assert (a / ".venv").is_dir()
    assert live["commits_a"].stdout.strip() == "1", live["commits_a"].stderr
    tree = _git("--git-dir", str(live["state_git"]), "ls-tree", "-r", "--name-only", "state")
    names = tree.stdout.split()
    assert "data/calendar.db" in names
    assert any(n.startswith("site/") for n in names)
    assert all(f"site/{c}.ics" in names for c in IDS)
    assert live["status_a"]["run"]["state_db"] == "fresh"


def test_second_run_stability(live):
    a, b = live["snap_a"], live["snap_b"]
    assert live["status_b"]["run"]["state_db"] == "restored"
    for c in IDS:  # UID 穩定、無重複
        ub = _uids(b[c])
        assert len(ub) == len(set(ub)), f"{c} duplicate UID"
        assert set(_uids(a[c])) <= set(ub), f"{c} UID disappeared"
    for c in RULE_BASED:
        assert set(_uids(a[c])) == set(_uids(b[c])), c
        assert a[c] == b[c], f"{c} not byte-identical"
    # 雜湊相同的事件，DTSTAMP / SEQUENCE 必須相同（證明 state 有還原）
    for c in ("tw-earnings", "us-earnings"):
        ea, eb = _events(a[c]), _events(b[c])
        same = 0
        for uid, blks_a in ea.items():
            if uid not in eb:
                continue
            if _content_hash(blks_a[0]) == _content_hash(eb[uid][0]):
                same += 1
                assert _field(blks_a[0], "DTSTAMP") == _field(eb[uid][0], "DTSTAMP"), (c, uid)
                assert _field(blks_a[0], "SEQUENCE") == _field(eb[uid][0], "SEQUENCE"), (c, uid)
        assert same > 0, f"{c}: no unchanged events to compare"
    assert live["commits_b"].stdout.strip() == "1", live["commits_b"].stderr


def test_serve_and_validate_subscriptions(live):
    from icalendar import Calendar

    site = live["kit_b"] / "site"
    status = json.loads((site / "status.json").read_text(encoding="utf-8"))

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", live["port"]), partial(Quiet, directory=str(site)))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        assert len(status["calendars"]) == 5
        for c in status["calendars"]:
            assert c["url"].startswith(live["site_url"]), c["url"]
            with urllib.request.urlopen(c["url"], timeout=10) as resp:
                assert resp.status == 200
                body = resp.read()
            cal = Calendar.from_ical(body)
            n = sum(1 for x in cal.walk() if x.name == "VEVENT")
            assert n == c["events"] and n > 0, (c["id"], n, c["events"])
        with urllib.request.urlopen(live["site_url"] + "index.html", timeout=10) as resp:
            html = resp.read().decode("utf-8")
        assert sum(1 for c in status["calendars"] if c["url"] in html) == 5
    finally:
        srv.shutdown()
        srv.server_close()


def test_report_source_status(live):
    """印出實際 MOPS / Yahoo / yfinance 狀態（供 Phase 6 報告引用，pytest -s 可見）。"""
    for tag in ("status_a", "status_b"):
        print(tag, json.dumps(live[tag]["sources"], ensure_ascii=False, sort_keys=True))
        print(tag, "calendars", {c["id"]: (c["last_attempt_status"], c["events"])
                                  for c in live[tag]["calendars"]})
    assert set(live["status_b"]["sources"]) == {"mops", "yahoo", "yfinance"}
