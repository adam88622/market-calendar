#!/bin/zsh
# 市場行事曆 — Mac 移除程式（雙擊執行）。不使用管理者權限；不會動其他行事曆。
# 環境變數：MCI_DRY_RUN=1（只印出不執行）、MCI_ASSUME_NO=1
setopt PIPE_FAIL
KIT="${0:A:h}"; cd "$KIT" || exit 2

ask() { [[ -n "${MCI_ASSUME_NO:-}" ]] && return 1; printf '%s [y/N] ' "$1"; local a; read -r a || { echo; return 1; }; [[ "$a" == [yY]* ]]; }
pause_exit() { [[ -t 0 ]] && { printf '按 Enter 關閉視窗…'; read -r _; }; exit "$1"; }
# run：乾跑模式只印出
run() { if [[ -n "${MCI_DRY_RUN:-}" ]]; then echo "[乾跑] $*"; else "$@"; fi; }

# 1. macOS 檢查與確認
if [[ "$(uname)" != Darwin ]]; then
  echo "此程式只適用 Mac。"
  pause_exit 2
fi
if ! ask "確定要移除自動更新排程嗎？"; then
  echo "已取消。"
  pause_exit 1
fi

# 2. LABEL
LABEL=""
if [[ -x .venv/bin/python ]]; then
  LABEL=$(.venv/bin/python -c 'from pathlib import Path; from core.config import load_config; print(load_config(Path("config.yaml"), Path(".")).schedule.label)' 2>/dev/null) || LABEL=""
fi
[[ -z "$LABEL" ]] && LABEL="com.market-calendar-ics.daily"
plist="$HOME/Library/LaunchAgents/$LABEL.plist"

# 3. 排程
OWN=0
if [[ ! -f "$plist" ]]; then
  echo "未安裝排程，無需處理"
else
  wd="$(plutil -extract WorkingDirectory raw -o - "$plist" 2>/dev/null)"
  if [[ "$wd" != "$KIT" ]]; then
    echo "排程屬於另一個資料夾（$wd），本程式不會動它"
  else
    OWN=1
    if [[ -x .venv/bin/python ]]; then
      run .venv/bin/python -m scheduler.launchd uninstall
    else
      run launchctl bootout "gui/$(id -u)/$LABEL"
      run rm -f "$plist"
    fi
  fi
fi

# 4. 行事曆授權紀錄（僅限排程屬於本資料夾時）
if [[ "$OWN" == 1 ]]; then
  if ask "要清除行事曆授權紀錄嗎？"; then
    run tccutil reset Calendar com.market-calendar-ics.sync
  fi
else
  echo "排程不屬於本資料夾，略過授權紀錄清除。"
fi

# 5. 本資料夾內的環境與資料
if ask "要刪除本資料夾內產生的環境與資料嗎（.venv、data、logs、output、MarketCalendarSync.app）？"; then
  for p in .venv data logs output scheduler/MarketCalendarSync.app; do
    if [[ -e "$KIT/$p" ]]; then
      run rm -rf "$KIT/$p"
    else
      echo "$p 已不存在"
    fi
  done
fi

# 6. 提醒
echo "行事曆 App 裡的 5 個行事曆不會自動刪除；不要的話，在行事曆 App 對該行事曆按右鍵 → 刪除。其他行事曆不受影響。"
pause_exit 0
