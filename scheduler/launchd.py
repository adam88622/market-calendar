"""launchd 排程：產生 plist、安裝與解除安裝（FN-017、FN-034）。

CLI：python -m scheduler.launchd {install|uninstall|print}
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from scheduler.app_bundle import build_app_bundle, default_app_path, launcher_path
from writer.atomic import write_atomic

DEFAULT_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def render_plist(label: str, prj_root: Path, hour: int, minute: int, *,
                 launcher: Path | None = None, mode: str = "direct",
                 app: Path | None = None,
                 times: Sequence[tuple[int, int]] | None = None) -> bytes:
    """產生 launchd plist 的 bytes。hour/minute 越界或參數組合非法丟 ValueError。

    times 給定時以 times 為準（單一時間為 dict、多個為 dict 陣列）；否則用 hour/minute。
    launcher 與 app 皆 None -> python + main.py；direct+launcher -> [launcher]；
    open+app -> open -W -g -a app；其他組合 ValueError。
    """
    if mode not in ("direct", "open"):
        raise ValueError(f"mode 必須是 direct|open：{mode!r}")
    if not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour <= 23:
        raise ValueError(f"hour 必須在 0..23：{hour!r}")
    if not isinstance(minute, int) or isinstance(minute, bool) or not 0 <= minute <= 59:
        raise ValueError(f"minute 必須在 0..59：{minute!r}")
    if times is None:
        slots = [(hour, minute)]
    else:
        slots = [tuple(t) for t in times]
        if not slots:
            raise ValueError("times 不可為空")
        for h, m in slots:
            if not isinstance(h, int) or isinstance(h, bool) or not 0 <= h <= 23:
                raise ValueError(f"hour 必須在 0..23：{h!r}")
            if not isinstance(m, int) or isinstance(m, bool) or not 0 <= m <= 59:
                raise ValueError(f"minute 必須在 0..59：{m!r}")
    intervals = [{"Hour": h, "Minute": m} for h, m in slots]
    prj = str(prj_root)
    if launcher is None and app is None:
        if mode != "direct":
            raise ValueError("mode=open 需要 app")
        args = [f"{prj}/.venv/bin/python", f"{prj}/main.py"]
    elif mode == "direct" and launcher is not None and app is None:
        args = [str(launcher)]
    elif mode == "open" and app is not None and launcher is None:
        args = ["/usr/bin/open", "-W", "-g", "-a", str(app)]
    else:
        raise ValueError(
            f"非法組合：mode={mode!r}, launcher={launcher!r}, app={app!r}")
    data = {
        "Label": label,
        "ProgramArguments": args,
        "WorkingDirectory": prj,
        "StartCalendarInterval": intervals[0] if len(intervals) == 1 else intervals,
        "StandardOutPath": f"{prj}/logs/launchd.out.log",
        "StandardErrorPath": f"{prj}/logs/launchd.err.log",
        "EnvironmentVariables": {"PYTHONIOENCODING": "utf-8"},
        "RunAtLoad": False,
    }
    return plistlib.dumps(data, sort_keys=True)


def plist_args_for(cfg: Any, prj_root: Path) -> dict:
    """依 apple_calendar.enabled 與 schedule.launch_mode 決定 render_plist 的關鍵字參數。

    install_agent 與 CLI print 共用；不做任何檔案操作。
    """
    ac = getattr(cfg, "apple_calendar", None)
    if not getattr(ac, "enabled", False):
        return {}
    app = default_app_path(Path(prj_root))
    if getattr(cfg.schedule, "launch_mode", "direct") == "open":
        return {"mode": "open", "app": app}
    return {"mode": "direct", "launcher": launcher_path(app)}


def _stderr(cp: Any) -> str:
    err = getattr(cp, "stderr", "") or ""
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    return str(err).strip()


def _run(run: Callable[..., Any], args: list[str]) -> Any:
    return run(args, capture_output=True, text=True)


def _system_timezone() -> str | None:
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return None
    marker = "zoneinfo/"
    idx = target.find(marker)
    return target[idx + len(marker):] if idx >= 0 else target


def install_agent(cfg: Any, prj_root: Path, agents_dir: Path = DEFAULT_AGENTS_DIR,
                  run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> Path:
    """安裝 LaunchAgent，回傳 plist 路徑。失敗丟 RuntimeError。"""
    prj_root = Path(prj_root)
    sch = cfg.schedule
    label = sch.label
    py = prj_root / ".venv" / "bin" / "python"
    if not py.exists():
        raise RuntimeError(f"找不到 venv python：{py}")
    (prj_root / "logs").mkdir(parents=True, exist_ok=True)

    plist_path = Path(agents_dir) / f"{label}.plist"
    kwargs = plist_args_for(cfg, prj_root)
    content = render_plist(label, prj_root, sch.hour, sch.minute,
                            times=getattr(sch, "times", None) or None, **kwargs)  # 先驗證參數
    if kwargs:  # apple 啟用才 build bundle
        build_app_bundle(prj_root, run=run)
    write_atomic(plist_path, content)

    cp = _run(run, ["plutil", "-lint", str(plist_path)])
    if cp.returncode != 0:
        raise RuntimeError(f"plutil -lint 失敗：{_stderr(cp) or getattr(cp, 'stdout', '')}")

    uid = os.getuid()
    domain = f"gui/{uid}"
    _run(run, ["launchctl", "bootout", f"{domain}/{label}"])  # 失敗可忽略
    cp = _run(run, ["launchctl", "bootstrap", domain, str(plist_path)])
    if cp.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap 失敗：{_stderr(cp)}")
    cp = _run(run, ["launchctl", "enable", f"{domain}/{label}"])
    if cp.returncode != 0:
        raise RuntimeError(f"launchctl enable 失敗：{_stderr(cp)}")

    tz = _system_timezone()
    if tz is not None and tz != "Asia/Taipei":
        print(f"WARNING: 系統時區為 {tz}，非 Asia/Taipei；"
              "StartCalendarInterval 依系統本地時間觸發。", file=sys.stderr)
    return plist_path


def uninstall_agent(cfg: Any, agents_dir: Path = DEFAULT_AGENTS_DIR,
                    run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> bool:
    """解除安裝。plist 不存在回傳 False；否則 bootout 後刪檔回傳 True。"""
    label = cfg.schedule.label
    plist_path = Path(agents_dir) / f"{label}.plist"
    if not plist_path.exists():
        return False
    _run(run, ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"])  # 未載入時失敗可忽略
    plist_path.unlink()
    return True


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in ("install", "uninstall", "print"):
        print("用法：python -m scheduler.launchd {install|uninstall|print}", file=sys.stderr)
        return 2
    cmd = argv[0]
    prj_root = Path(__file__).resolve().parent.parent
    from core.config import load_config

    try:
        cfg = load_config(prj_root / "config.yaml", prj_root)
        if cmd == "print":
            sch = cfg.schedule
            sys.stdout.write(render_plist(sch.label, prj_root, sch.hour, sch.minute,
                                          times=getattr(sch, "times", None) or None,
                                          **plist_args_for(cfg, prj_root)).decode("utf-8"))
        elif cmd == "install":
            path = install_agent(cfg, prj_root)
            print(f"已安裝：{path}")
        else:
            removed = uninstall_agent(cfg)
            print("已解除安裝" if removed else "未安裝，無需處理")
    except (RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
