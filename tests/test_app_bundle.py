import os
import plistlib
import shutil
import stat
import subprocess
import sys

import pytest
from pathlib import Path

from scheduler.app_bundle import build_app_bundle, default_app_path


class FakeRun:
    def __init__(self, rc=0):
        self.calls = []
        self.rc = rc

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        if self.rc == 0 and args[0].endswith("clang"):
            Path(args[args.index("-o") + 1]).write_bytes(b"bin")
        return subprocess.CompletedProcess(args, self.rc, stdout="",
                                           stderr="bad sign" if self.rc else "")


def test_build_contents(tmp_path):
    prj = tmp_path / "my prj"
    run = FakeRun()
    app = build_app_bundle(prj, run=run)
    assert app == prj / "scheduler" / "MarketCalendarSync.app" == default_app_path(prj)
    d = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert d["CFBundleIdentifier"] == "com.market-calendar-ics.sync"
    assert d["CFBundleName"] == "MarketCalendarSync"
    assert d["CFBundleExecutable"] == "launcher"
    assert d["CFBundlePackageType"] == "APPL"
    assert d["LSUIElement"] is True
    txt = "每日把台美股結算、法說與財報事件寫入行事曆"
    assert d["NSCalendarsFullAccessUsageDescription"] == txt
    assert d["NSCalendarsUsageDescription"] == txt
    launcher = app / "Contents" / "MacOS" / "launcher"
    src = app.parent / "MarketCalendarSync.launcher.c"
    assert run.calls == [
        ["/usr/bin/clang", "-O2", "-o", str(launcher), str(src)],
        ["codesign", "--force", "--deep", "-s", "-", str(app)],
    ]
    c = src.read_text(encoding="utf-8")
    assert f'chdir' in c and f'"{prj}"' in c
    assert f'"{prj}/.venv/bin/python"' in c and f'"{prj}/main.py"' in c
    assert "posix_spawn" in c and "waitpid" in c and "128 + WTERMSIG" in c
    assert "exec" not in c.replace("WIFEXITED", "").replace("WEXITSTATUS", "")
    assert not any(p.suffix == ".c" for p in (app / "Contents").rglob("*"))


def test_c_escape():
    from scheduler.app_bundle import _c_escape, _launcher_source
    assert _c_escape('a\\b"c') == 'a\\\\b\\"c'
    assert '"/x y/z"' in _launcher_source("/x y/z")


def test_clang_missing(tmp_path):
    def run(args, **kw):
        raise FileNotFoundError("clang")
    with pytest.raises(RuntimeError, match="xcode-select --install"):
        build_app_bundle(tmp_path, run=run)


def test_clang_failure(tmp_path):
    class R(FakeRun):
        def __call__(self, args, **kw):
            super().__call__(args, **kw)
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")
    with pytest.raises(RuntimeError, match="xcode-select --install"):
        build_app_bundle(tmp_path, run=R())


def test_custom_app_dir_and_idempotent(tmp_path):
    app_dir = tmp_path / "X.app"
    run = FakeRun()
    a1 = build_app_bundle(tmp_path, app_dir=app_dir, run=run)
    (a1 / "Contents" / "MacOS" / "launcher").write_text("junk")
    a2 = build_app_bundle(tmp_path, app_dir=app_dir, run=run)
    assert a1 == a2 == app_dir
    assert (a2 / "Contents" / "MacOS" / "launcher").read_bytes() == b"bin"
    assert len(run.calls) == 4


def test_codesign_failure(tmp_path):
    class R(FakeRun):
        def __call__(self, args, **kw):
            super().__call__(args, **kw)
            if args[0] == "codesign":
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="bad sign")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    with pytest.raises(RuntimeError, match="bad sign"):
        build_app_bundle(tmp_path, run=R())


def test_bad_path_chars(tmp_path):
    with pytest.raises(ValueError):
        build_app_bundle(tmp_path / 'a"b', run=FakeRun())


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("codesign")
                    or not os.path.exists("/usr/bin/clang"),
                    reason="needs macOS codesign + clang")
def test_real_codesign_and_lint(tmp_path):
    (tmp_path / "prj").mkdir()
    app = build_app_bundle(tmp_path / "prj")
    r = subprocess.run(["codesign", "-dv", str(app)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "com.market-calendar-ics.sync" in r.stderr
    r = subprocess.run(["plutil", "-lint", str(app / "Contents" / "Info.plist")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("codesign")
                    or not os.path.exists("/usr/bin/clang"),
                    reason="needs macOS codesign + clang")
def test_real_launcher_runs(tmp_path):
    prj = tmp_path / "fake prj"
    py = prj / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    out = tmp_path / "argv.txt"
    py.write_text(f'#!/bin/sh\nprintf "%s\\n" "$PWD" "$@" > "{out}"\nexit 7\n')
    py.chmod(0o755)
    app = build_app_bundle(prj)
    launcher = app / "Contents" / "MacOS" / "launcher"
    r = subprocess.run([str(launcher), "--x", "a b"])
    assert r.returncode == 7
    lines = out.read_text().splitlines()
    assert lines[0] == str(prj.resolve()) or lines[0] == str(prj)
    assert lines[1:] == [f"{prj}/main.py", "--x", "a b"]
    r = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
