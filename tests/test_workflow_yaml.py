"""F-010：publish.yml 結構檢查（離線，R19 斷言清單）。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

PRJ = Path(__file__).resolve().parent.parent
WF = PRJ / ".github" / "workflows" / "publish.yml"


@pytest.fixture(scope="module")
def raw() -> str:
    return WF.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def wf(raw) -> dict:
    return yaml.safe_load(raw)


def _on(wf):
    return wf["on"] if "on" in wf else wf[True]


def _steps(job):
    return job.get("steps", [])


def _uses(wf):
    for name, job in wf["jobs"].items():
        for st in _steps(job):
            if "uses" in st:
                yield name, st


def _needs(job):
    n = job.get("needs", [])
    return [n] if isinstance(n, str) else list(n)


def test_schedule_four_crons_match_config(wf):
    crons = [s["cron"] for s in _on(wf)["schedule"]]
    assert len(crons) == 4
    taipei = set()
    for c in crons:
        m, h, *rest = c.split()
        assert rest == ["*", "*", "*"]
        taipei.add(f"{(int(h) + 8) % 24:02d}:{int(m):02d}")
    assert taipei == {"08:30", "12:30", "19:30", "21:30"}
    cfg_path = PRJ / "config.cloud.yaml"
    if not cfg_path.exists():
        cfg_path = PRJ / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert taipei == set(cfg["schedule"]["times"])


def test_workflow_dispatch(wf):
    assert "workflow_dispatch" in _on(wf)


def test_permissions(wf):
    jobs = wf["jobs"]
    assert wf["permissions"] == {}
    assert jobs["build"]["permissions"] == {"contents": "read"}
    assert jobs["save-state"]["permissions"] == {"contents": "write"}
    assert jobs["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}
    assert "alert" not in jobs


def test_save_state_only_official_actions(wf):
    uses = [st["uses"] for st in _steps(wf["jobs"]["save-state"]) if "uses" in st]
    assert uses
    assert all(u.startswith("actions/") for u in uses)
    assert "actions/download-artifact@v4" in uses
    assert _needs(wf["jobs"]["save-state"]) == ["build"]


def test_deploy_needs_and_environment(wf):
    deploy = wf["jobs"]["deploy"]
    assert set(_needs(deploy)) == {"build", "save-state"}
    assert deploy["environment"]["name"] == "github-pages"


def test_concurrency(wf):
    assert wf["concurrency"]["group"]
    assert wf["concurrency"]["cancel-in-progress"] is False


def test_timeouts(wf):
    for name, job in wf["jobs"].items():
        assert job["timeout-minutes"] <= 30, name


def test_uses_pinned(wf):
    seen = {}
    for _, st in _uses(wf):
        u = st["uses"]
        assert re.search(r"@(v\d+|[0-9a-f]{40})$", u), u
        seen[u.split("@")[0]] = u
    assert re.fullmatch(r"astral-sh/setup-uv@[0-9a-f]{40}", seen["astral-sh/setup-uv"])
    # 第三方 action 只允許 setup-uv，且一律 40 位 SHA
    for name, u in seen.items():
        if not name.startswith("actions/"):
            assert name == "astral-sh/setup-uv"
            assert re.search(r"@[0-9a-f]{40}$", u)


def test_setup_uv_version_and_no_token(wf):
    st = next(s for _, s in _uses(wf) if s["uses"].startswith("astral-sh/setup-uv@"))
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(st["with"]["version"]))
    assert "github-token" not in st["with"]


def test_python_311(raw):
    assert "--python 3.11" in raw


def test_run_blocks_clean(wf, raw):
    for job in wf["jobs"].values():
        for st in _steps(job):
            run = st.get("run")
            if run:
                assert "${{" not in run
                assert not re.search(r"\bsudo\b", run)
    assert "secrets." not in raw


def test_command_lines(wf):
    runs = "\n".join(st["run"] for st in _steps(wf["jobs"]["build"]) if "run" in st)
    assert ".venv/bin/python main.py --config config.cloud.yaml --report run-report.json" in runs
    assert ("python -m cloud.publish --config config.cloud.yaml --report run-report.json "
            "--prev-dir prev-site --restore-info restore-info.json "
            '--site-url "$SITE_URL" --exit-code "$RUN_EXIT_CODE"') in runs
    assert ("python -m cloud.state restore --config config.cloud.yaml --state-dir state-in "
            "--prev-dir prev-site --info-out restore-info.json") in runs
    assert "cloud.state save --config config.cloud.yaml --state-dir state-payload" in runs
    assert "MCI_STATE_REMOTE" in runs


def test_build_artifacts(wf):
    steps = _steps(wf["jobs"]["build"])
    pages = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-pages-artifact@")]
    assert len(pages) == 1 and pages[0]["with"]["path"] == "site"
    ups = {s["with"]["name"]: s for s in steps
           if str(s.get("uses", "")).startswith("actions/upload-artifact@")}
    sp = ups["state-payload"]
    assert sp["uses"] == "actions/upload-artifact@v4"
    assert sp["with"]["retention-days"] == 1
    assert sp["with"]["path"].rstrip("/") == "state-payload"
    logs = next(s for n, s in ups.items() if n.startswith("run-logs"))
    paths = logs["with"]["path"].split()
    assert paths == ["cloud-logs/", "run-report.json", "restore-info.json"]
    dl = next(s for s in _steps(wf["jobs"]["save-state"]) if "uses" in s)
    assert dl["with"]["name"] == "state-payload" and dl["with"]["path"] == "state-out"


def test_state_branch_push_uses_env_token(wf):
    run = next(s["run"] for s in _steps(wf["jobs"]["save-state"]) if "run" in s)
    assert "git init --quiet --initial-branch=" in run
    assert "http.extraheader" in run
    assert 'HEAD:refs/heads/$STATE_BRANCH' in run
    assert "push --force" in run
    step = next(s for s in _steps(wf["jobs"]["save-state"]) if "run" in s)
    assert "GH_TOKEN" in step["env"]


def test_kit_copy_identical():
    kit = PRJ / "dist" / "market-calendar-kit" / "cloud-setup" / "publish.yml"
    if kit.exists():
        assert kit.read_bytes() == WF.read_bytes()
