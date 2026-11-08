"""Mac .command 腳本靜態檢查（不實際執行）。"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PRJ = Path(__file__).resolve().parent.parent
INSTALL = PRJ / "安裝-Mac.command"
REMOVE = PRJ / "移除-Mac.command"
FILES = [INSTALL, REMOVE]


@pytest.mark.parametrize("f", FILES, ids=lambda p: p.name)
def test_exists_and_executable(f):
    assert f.is_file()
    assert os.access(f, os.X_OK)
    assert (f.stat().st_mode & 0o777) == 0o755


@pytest.mark.parametrize("f", FILES, ids=lambda p: p.name)
def test_shebang_and_no_sudo(f):
    text = f.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/zsh")
    assert "sudo" not in text.lower()


@pytest.mark.skipif(not Path("/bin/zsh").exists(), reason="no zsh")
@pytest.mark.parametrize("f", FILES, ids=lambda p: p.name)
def test_zsh_syntax(f):
    r = subprocess.run(["/bin/zsh", "-n", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_install_keywords():
    t = INSTALL.read_text(encoding="utf-8")
    for kw in ["curl -LsSf https://astral.sh/uv/install.sh | sh", "xcode-select --install",
               "MCI_INSTALL_STOP_AFTER", "not live and not eventkit", "--authorize"]:
        assert kw in t, kw


def test_remove_keywords():
    t = REMOVE.read_text(encoding="utf-8")
    for kw in ["MCI_DRY_RUN", "WorkingDirectory", "tccutil reset Calendar com.market-calendar-ics.sync"]:
        assert kw in t, kw
