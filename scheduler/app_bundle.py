"""產生 MarketCalendarSync.app 啟動器（FN-059）。

讓 launchd 以 .app bundle 身分執行，使行事曆授權（TCC）歸屬到 bundle。
"""
from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path
from typing import Callable

APP_NAME = "MarketCalendarSync.app"
BUNDLE_ID = "com.market-calendar-ics.sync"
USAGE_DESC = "每日把台美股結算、法說與財報事件寫入行事曆"
CLANG = "/usr/bin/clang"
_FORBIDDEN = set('"$`\\\n')


def default_app_path(prj_root: Path) -> Path:
    return Path(prj_root) / "scheduler" / APP_NAME


def launcher_path(app: Path) -> Path:
    return Path(app) / "Contents" / "MacOS" / "launcher"


def _info_plist() -> bytes:
    return plistlib.dumps({
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": "MarketCalendarSync",
        "CFBundleExecutable": "launcher",
        "CFBundlePackageType": "APPL",
        "LSUIElement": True,
        "NSCalendarsFullAccessUsageDescription": USAGE_DESC,
        "NSCalendarsUsageDescription": USAGE_DESC,
    }, sort_keys=True)


def _c_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _launcher_source(prj: str) -> str:
    """C 原始碼：chdir、posix_spawn python main.py + argv[1:]、waitpid、回傳退出碼。"""
    p = _c_escape(prj)
    return f'''#include <spawn.h>
#include <errno.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
extern char **environ;
int main(int argc, char **argv) {{
    const char *prj = "{p}";
    const char *py = "{p}/.venv/bin/python";
    const char *script = "{p}/main.py";
    if (chdir(prj) != 0) return 126;
    char **a = calloc((size_t)argc + 3, sizeof(char *));
    if (!a) return 126;
    int n = 0;
    a[n++] = (char *)py;
    a[n++] = (char *)script;
    for (int i = 1; i < argc; i++) a[n++] = argv[i];
    a[n] = NULL;
    pid_t pid;
    if (posix_spawn(&pid, py, NULL, NULL, a, environ) != 0) return 127;
    int st = 0;
    while (waitpid(pid, &st, 0) < 0) {{
        if (errno != EINTR) return 1;
    }}
    if (WIFEXITED(st)) return WEXITSTATUS(st);
    if (WIFSIGNALED(st)) return 128 + WTERMSIG(st);
    return 1;
}}
'''


def _fail_text(cp) -> str:
    err = getattr(cp, "stderr", "") or ""
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    return str(err).strip()


def build_app_bundle(prj_root: Path, app_dir: Path | None = None,
                     run: Callable = subprocess.run) -> Path:
    """建立並 ad-hoc 簽章 .app（Mach-O launcher），回傳 app 路徑。冪等。

    clang 不存在/編譯失敗、codesign 失敗皆丟 RuntimeError。
    """
    prj_root = Path(prj_root)
    prj = str(prj_root)
    if _FORBIDDEN & set(prj):
        raise ValueError(f"專案路徑含不支援字元：{prj!r}")
    app = Path(app_dir) if app_dir is not None else default_app_path(prj_root)
    macos = app / "Contents" / "MacOS"
    src = app.parent / "MarketCalendarSync.launcher.c"
    try:
        macos.mkdir(parents=True, exist_ok=True)
        (app / "Contents" / "Info.plist").write_bytes(_info_plist())
        src.write_text(_launcher_source(prj), encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"建立 app bundle 失敗：{e}") from e

    launcher = launcher_path(app)
    hint = "請安裝 Xcode Command Line Tools：xcode-select --install"
    try:
        cp = run([CLANG, "-O2", "-o", str(launcher), str(src)],
                 capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RuntimeError(f"找不到 clang。{hint}") from e
    if cp.returncode != 0:
        raise RuntimeError(f"clang 編譯 launcher 失敗：{_fail_text(cp)}。{hint}")

    cp = run(["codesign", "--force", "--deep", "-s", "-", str(app)],
             capture_output=True, text=True)
    if cp.returncode != 0:
        raise RuntimeError(f"codesign 失敗：{_fail_text(cp)}")
    return app
