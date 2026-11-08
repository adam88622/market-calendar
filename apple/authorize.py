"""FN-056：授權流程（架構 v4 V4-5 + v4.1 R10、R16 建議 3）。

結果同時印到 out 並寫入 logs/authorize-YYYYMMDD-HHMMSS.log
（以 .app 身分執行時沒有終端機，靠這個檔看結果）。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, TextIO

from apple.adapter import CalendarAdapter
from apple.eventkit_adapter import EventKitAdapter

PRJ_ROOT = Path(__file__).resolve().parent.parent


def _steps(prj_root: Path) -> str:
    app = prj_root / "scheduler" / "MarketCalendarSync.app"
    return (
        "請依下列步驟處理：\n"
        "  1. 系統設定 → 隱私權與安全性 → 行事曆 → 把『MarketCalendarSync』"
        "（或執行這個指令的終端機 App：Terminal／iTerm）設為『完整取用』。\n"
        "  2. 需要重置時：tccutil reset Calendar com.market-calendar-ics.sync"
        "（只重置本專案 .app）；終端機身分則用 tccutil reset Calendar com.apple.Terminal"
        "（只會重置 Terminal 的行事曆權限）。\n"
        f"  3. 重新授權指令：open \"{app}\" --args --authorize\n"
    )


def authorize(
    adapter: CalendarAdapter | None = None,
    *,
    timeout_sec: float = 120.0,
    out: TextIO = sys.stdout,
    log_dir: Path | None = None,
    now: Callable[[], datetime] = datetime.now,
    prj_root: Path | None = None,
) -> int:
    root = Path(prj_root) if prj_root else PRJ_ROOT
    lines: list[str] = []

    def say(msg: str) -> None:
        lines.append(msg)
        try:
            print(msg, file=out)
            out.flush()
        except Exception:  # noqa: BLE001
            pass

    def finish(code: int) -> int:
        say(f"exit code: {code}")
        try:
            d = Path(log_dir) if log_dir else root / "logs"
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"authorize-{now().strftime('%Y%m%d-%H%M%S')}.log"
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return code

    if adapter is None:
        try:
            adapter = EventKitAdapter.create()
        except Exception as e:  # noqa: BLE001
            say(f"無法初始化 EventKit：{e}")
            return finish(2)

    try:
        status = adapter.authorization_status()
    except Exception as e:  # noqa: BLE001
        say(f"無法讀取授權狀態：{e}")
        return finish(2)
    say(f"目前行事曆授權狀態：{status}")

    if status == "fullAccess":
        say("已授權（fullAccess）。")
        return finish(0)

    if status == "notDetermined":
        say("正在要求行事曆完整存取權限，請在跳出的視窗按『允許完整取用』…")
        granted, error = adapter.request_full_access(timeout_sec)
        say(f"granted={granted} error={error}")
        try:
            status = adapter.authorization_status()
        except Exception as e:  # noqa: BLE001
            say(f"無法讀取授權狀態：{e}")
            return finish(1)
        say(f"授權後狀態：{status}")
        if status == "fullAccess":
            say("已授權（fullAccess）。")
            return finish(0)
        say(_steps(root))
        return finish(1)

    # denied、restricted、writeOnly
    say(f"未取得完整存取權限（status={status}）。")
    say(_steps(root))
    return finish(1)
