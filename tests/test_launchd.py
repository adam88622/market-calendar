import plistlib
import subprocess
import types
from pathlib import Path

import pytest

from scheduler import launchd
from scheduler.launchd import install_agent, render_plist, uninstall_agent

LABEL = "com.market-calendar-ics.daily"


def make_cfg(hour=7, minute=30):
    return types.SimpleNamespace(
        schedule=types.SimpleNamespace(label=LABEL, hour=hour, minute=minute))


class FakeRun:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or {}

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        key = args[1] if args[0] == "launchctl" else args[0]
        rc = self.fail.get(key, 0)
        if rc == 0 and args[0].endswith("clang"):
            Path(args[args.index("-o") + 1]).write_bytes(b"bin")
        return subprocess.CompletedProcess(args, rc, stdout="", stderr="boom" if rc else "")


@pytest.fixture
def prj(tmp_path):
    p = tmp_path / "prj"
    (p / ".venv" / "bin").mkdir(parents=True)
    (p / ".venv" / "bin" / "python").write_text("")
    return p


def test_render_plist_fields(tmp_path):
    prj = tmp_path / "x"
    d = plistlib.loads(render_plist(LABEL, prj, 7, 30))
    assert d["Label"] == LABEL
    assert d["ProgramArguments"] == [f"{prj}/.venv/bin/python", f"{prj}/main.py"]
    assert d["WorkingDirectory"] == str(prj)
    assert d["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}
    assert d["StandardOutPath"] == f"{prj}/logs/launchd.out.log"
    assert d["StandardErrorPath"] == f"{prj}/logs/launchd.err.log"
    assert d["EnvironmentVariables"] == {"PYTHONIOENCODING": "utf-8"}
    assert d["RunAtLoad"] is False


@pytest.mark.parametrize("h,m", [(-1, 0), (24, 0), (0, -1), (0, 60)])
def test_render_plist_range(h, m, tmp_path):
    with pytest.raises(ValueError):
        render_plist(LABEL, tmp_path, h, m)


def test_plutil_lint(tmp_path):
    f = tmp_path / "a.plist"
    f.write_bytes(render_plist(LABEL, tmp_path, 7, 30))
    r = subprocess.run(["plutil", "-lint", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_install_order(prj, tmp_path):
    agents = tmp_path / "agents"
    run = FakeRun()
    path = install_agent(make_cfg(), prj, agents_dir=agents, run=run)
    assert path == agents / f"{LABEL}.plist"
    d = plistlib.loads(path.read_bytes())
    assert d["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}
    assert (prj / "logs").is_dir()
    seq = [c[0] if c[0] == "plutil" else c[1] for c in run.calls]
    assert seq == ["plutil", "bootout", "bootstrap", "enable"]
    assert run.calls[2][3] == str(path)


def test_install_bootout_failure_ignored(prj, tmp_path):
    run = FakeRun(fail={"bootout": 3})
    install_agent(make_cfg(), prj, agents_dir=tmp_path / "a", run=run)


def test_install_bootstrap_failure(prj, tmp_path):
    with pytest.raises(RuntimeError, match="boom"):
        install_agent(make_cfg(), prj, agents_dir=tmp_path / "a", run=FakeRun(fail={"bootstrap": 5}))


def test_install_lint_failure(prj, tmp_path):
    with pytest.raises(RuntimeError, match="boom"):
        install_agent(make_cfg(), prj, agents_dir=tmp_path / "a", run=FakeRun(fail={"plutil": 1}))


def test_install_no_venv(tmp_path):
    with pytest.raises(RuntimeError):
        install_agent(make_cfg(), tmp_path, agents_dir=tmp_path / "a", run=FakeRun())


def test_install_bad_hour(prj, tmp_path):
    with pytest.raises(ValueError):
        install_agent(make_cfg(hour=25), prj, agents_dir=tmp_path / "a", run=FakeRun())


def test_uninstall(prj, tmp_path):
    agents = tmp_path / "agents"
    assert uninstall_agent(make_cfg(), agents_dir=agents, run=FakeRun()) is False
    install_agent(make_cfg(), prj, agents_dir=agents, run=FakeRun())
    run = FakeRun()
    assert uninstall_agent(make_cfg(), agents_dir=agents, run=run) is True
    assert not (agents / f"{LABEL}.plist").exists()
    assert run.calls[0][:2] == ["launchctl", "bootout"]


def test_uninstall_missing_runs_nothing(tmp_path):
    run = FakeRun()
    assert uninstall_agent(make_cfg(), agents_dir=tmp_path, run=run) is False
    assert run.calls == []


# ---- v4.1 R16：launcher / mode / app ----
from scheduler.launchd import plist_args_for  # noqa: E402


def make_cfg2(apple=True, mode="direct"):
    return types.SimpleNamespace(
        schedule=types.SimpleNamespace(label=LABEL, hour=7, minute=30, launch_mode=mode),
        apple_calendar=types.SimpleNamespace(enabled=apple))


def test_render_plist_launcher_direct(tmp_path):
    launcher = tmp_path / "A.app/Contents/MacOS/launcher"
    d = plistlib.loads(render_plist(LABEL, tmp_path, 7, 30, launcher=launcher))
    assert d["ProgramArguments"] == [str(launcher)]


def test_render_plist_open_mode(tmp_path):
    app = tmp_path / "A.app"
    d = plistlib.loads(render_plist(LABEL, tmp_path, 7, 30, mode="open", app=app))
    assert d["ProgramArguments"] == ["/usr/bin/open", "-W", "-g", "-a", str(app)]


def test_render_plist_default_args(tmp_path):
    d = plistlib.loads(render_plist(LABEL, tmp_path, 7, 30, mode="direct"))
    assert d["ProgramArguments"] == [f"{tmp_path}/.venv/bin/python", f"{tmp_path}/main.py"]


@pytest.mark.parametrize("kw", [
    {"mode": "open"},                                   # open 無 app
    {"mode": "direct", "app": Path("/x.app")},          # direct 只有 app
    {"mode": "open", "launcher": Path("/l")},           # open 只有 launcher
    {"mode": "direct", "launcher": Path("/l"), "app": Path("/x.app")},
    {"mode": "open", "launcher": Path("/l"), "app": Path("/x.app")},
    {"mode": "bogus"},
])
def test_render_plist_illegal_combo(kw, tmp_path):
    with pytest.raises(ValueError):
        render_plist(LABEL, tmp_path, 7, 30, **kw)


def test_plist_args_for(tmp_path):
    app = tmp_path / "scheduler" / "MarketCalendarSync.app"
    assert plist_args_for(make_cfg2(apple=False), tmp_path) == {}
    assert plist_args_for(make_cfg(), tmp_path) == {}  # 無 apple_calendar 屬性
    assert plist_args_for(make_cfg2(True, "direct"), tmp_path) == {
        "mode": "direct", "launcher": app / "Contents" / "MacOS" / "launcher"}
    assert plist_args_for(make_cfg2(True, "open"), tmp_path) == {"mode": "open", "app": app}


def test_install_apple_disabled_no_bundle(prj, tmp_path):
    run = FakeRun()
    path = install_agent(make_cfg2(apple=False), prj, agents_dir=tmp_path / "a", run=run)
    assert not any(c[0] == "codesign" for c in run.calls)
    assert not (prj / "scheduler" / "MarketCalendarSync.app").exists()
    assert plistlib.loads(path.read_bytes())["ProgramArguments"] == [
        f"{prj}/.venv/bin/python", f"{prj}/main.py"]


@pytest.mark.parametrize("mode", ["direct", "open"])
def test_install_apple_enabled_builds_bundle(prj, tmp_path, mode):
    run = FakeRun()
    path = install_agent(make_cfg2(True, mode), prj, agents_dir=tmp_path / "a", run=run)
    app = prj / "scheduler" / "MarketCalendarSync.app"
    seq = [c[0] if c[0] in ("plutil", "codesign", "/usr/bin/clang") else c[1]
           for c in run.calls]
    assert seq == ["/usr/bin/clang", "codesign", "plutil", "bootout", "bootstrap", "enable"]
    assert (app / "Contents" / "MacOS" / "launcher").exists()
    args = plistlib.loads(path.read_bytes())["ProgramArguments"]
    if mode == "direct":
        assert args == [str(app / "Contents" / "MacOS" / "launcher")]
    else:
        assert args == ["/usr/bin/open", "-W", "-g", "-a", str(app)]


@pytest.mark.parametrize("apple,mode", [(False, "direct"), (True, "direct"), (True, "open")])
def test_print_equals_install_bytes(prj, tmp_path, apple, mode):
    cfg = make_cfg2(apple, mode)
    path = install_agent(cfg, prj, agents_dir=tmp_path / "a", run=FakeRun())
    printed = render_plist(LABEL, prj, 7, 30, **plist_args_for(cfg, prj))
    assert printed == path.read_bytes()


def test_render_plist_multi_times(tmp_path):
    t = [(8, 30), (12, 30), (19, 30), (21, 30)]
    d = plistlib.loads(render_plist(LABEL, tmp_path, 21, 0, times=t))
    assert d["StartCalendarInterval"] == [
        {"Hour": 8, "Minute": 30}, {"Hour": 12, "Minute": 30},
        {"Hour": 19, "Minute": 30}, {"Hour": 21, "Minute": 30}]


def test_render_plist_single_time_stays_dict(tmp_path):
    d = plistlib.loads(render_plist(LABEL, tmp_path, 21, 0, times=[(7, 30)]))
    assert d["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}


@pytest.mark.parametrize("t", [[], [(24, 0)], [(8, 60)]])
def test_render_plist_times_invalid(t, tmp_path):
    with pytest.raises(ValueError):
        render_plist(LABEL, tmp_path, 7, 30, times=t)
