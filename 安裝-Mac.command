#!/bin/zsh
# 市場行事曆 — Mac 一鍵安裝（雙擊執行）。不使用管理者權限；不會修改其他行事曆。
# 環境變數：MCI_INSTALL_STOP_AFTER=tests（測試後停止）、MCI_SKIP_TESTS=1、MCI_ASSUME_NO=1
setopt PIPE_FAIL
KIT="${0:A:h}"; cd "$KIT" || exit 2
mkdir -p logs; exec > >(tee -a "logs/install-$(date +%Y%m%d-%H%M%S).log") 2>&1

ask() { [[ -n "${MCI_ASSUME_NO:-}" ]] && return 1; printf '%s [y/N] ' "$1"; local a; read -r a || { echo; return 1; }; [[ "$a" == [yY]* ]]; }
pause_exit() { [[ -t 0 ]] && { printf '按 Enter 關閉視窗…'; read -r _; }; exit "$1"; }

# 1. macOS 檢查
if [[ "$(uname)" != Darwin ]]; then
  echo "此安裝程式只適用 Mac；Windows 請用訂閱網址。"
  pause_exit 2
fi

# 2. 路徑檢查
case "$KIT" in
  *\"*|*\$*|*\`*|*\\*|*$'\n'*)
    echo "資料夾路徑含有特殊字元（引號、\$、反引號、反斜線或換行），請把資料夾改名或移到單純的路徑後再執行。"
    pause_exit 2 ;;
esac
for protected in "$HOME/Desktop" "$HOME/Documents" "$HOME/Downloads" "$HOME/Library/Mobile Documents"; do
  if [[ "$KIT" == "$protected"/* || "$KIT" == "$protected" ]]; then
    echo "背景排程沒有權限讀取「桌面、文件、下載、iCloud 雲碟」裡的資料夾。"
    if ask "要把資料夾搬到 ~/market-calendar-kit 嗎？"; then
      if [[ -e "$HOME/market-calendar-kit" ]]; then
        echo "~/market-calendar-kit 已經存在，請手動處理（改名或移走）後再執行。"
        pause_exit 1
      fi
      cd "$HOME" && mv "$KIT" "$HOME/market-calendar-kit" && exec /bin/zsh "$HOME/market-calendar-kit/安裝-Mac.command"
      echo "搬移失敗，請手動把資料夾移到家目錄。"
      pause_exit 1
    fi
    pause_exit 1
  fi
done

# 3. Xcode Command Line Tools
if ! { xcode-select -p >/dev/null 2>&1 && xcrun --find clang >/dev/null 2>&1; }; then
  echo "需要 Xcode 指令列工具（用來建立行事曆授權用的小程式）。"
  if ask "要現在安裝嗎？（會跳出系統視窗，不需要管理者密碼）"; then
    xcode-select --install
    echo "安裝完成後請再雙擊一次本檔。"
  fi
  pause_exit 1
fi

# 4. uv
find_uv() {
  UV="$(command -v uv 2>/dev/null)"
  [[ -z "$UV" && -x "$HOME/.local/bin/uv" ]] && UV="$HOME/.local/bin/uv"
  [[ -z "$UV" && -x "$HOME/.cargo/bin/uv" ]] && UV="$HOME/.cargo/bin/uv"
  [[ -n "$UV" ]]
}
if ! find_uv; then
  echo "找不到 uv（Python 套件工具）。官方安裝指令："
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "會安裝到 ~/.local/bin，不需要管理者權限。"
  if ask "要現在執行嗎？"; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    if ! find_uv; then
      echo "uv 安裝失敗。"
      pause_exit 3
    fi
  else
    pause_exit 1
  fi
fi
echo "使用 uv：$UV"

# 5. 建立 .venv
if ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3,11))' >/dev/null 2>&1; then
  rm -rf "$KIT/.venv"
  "$UV" venv --python 3.11 .venv || { echo "建立 Python 環境失敗。"; pause_exit 3; }
fi

# 6. 安裝套件
"$UV" pip install --python .venv/bin/python -r requirements.txt || { echo "安裝套件失敗。"; pause_exit 3; }

# 7. 自我測試
if [[ -z "${MCI_SKIP_TESTS:-}" ]]; then
  echo "執行自我測試（約 1 到 2 分鐘）…"
  if ! .venv/bin/python -m pytest -q -m "not live and not eventkit"; then
    echo "測試失敗，請查看日誌：$KIT/logs/ 內最新的 install-*.log"
    pause_exit 4
  fi
  echo "測試通過"
fi

# 8. 提前結束
if [[ "${MCI_INSTALL_STOP_AFTER:-}" == tests ]]; then
  echo "已依 MCI_INSTALL_STOP_AFTER=tests 停止（未安裝排程）"
  exit 0
fi

# 9. 既有安裝偵測
LABEL=$(.venv/bin/python -c 'from pathlib import Path; from core.config import load_config; print(load_config(Path("config.yaml"), Path(".")).schedule.label)') || LABEL=""
[[ -z "$LABEL" ]] && LABEL="com.market-calendar-ics.daily"
plist="$HOME/Library/LaunchAgents/$LABEL.plist"
if [[ -f "$plist" ]]; then
  old_wd="$(plutil -extract WorkingDirectory raw -o - "$plist" 2>/dev/null)"
  if [[ "$old_wd" != "$KIT" ]]; then
    echo "已在另一個資料夾安裝過：$old_wd。繼續會改用這個資料夾。"
    ask "要繼續嗎？" || pause_exit 1
  fi
fi

# 10. 安裝排程
chmod 755 scripts/*.sh
/bin/bash scripts/install.sh || { echo "安裝排程失敗。"; pause_exit 5; }

# 11. 行事曆授權
started="$(date +%s)"
open "$KIT/scheduler/MarketCalendarSync.app" --args --authorize
echo "請在跳出的視窗按「允許完整取用」。"
found=""
for i in {1..60}; do
  for f in logs/authorize-*.log(N); do
    mt="$(stat -f %m "$f" 2>/dev/null || echo 0)"
    [[ "$mt" -ge "$started" ]] && found="$f"
  done
  [[ -n "$found" ]] && break
  sleep 2
done
if [[ -n "$found" ]]; then
  tail -n 5 "$found"
  if ! grep -q fullAccess "$found"; then
    echo "尚未取得授權。請到：系統設定 → 隱私權與安全性 → 行事曆 → 開啟 MarketCalendarSync"
  else
    echo "行事曆授權成功。"
  fi
else
  echo "沒有等到授權結果。請到：系統設定 → 隱私權與安全性 → 行事曆 → 開啟 MarketCalendarSync"
fi

# 12. 第一次同步
if ask "要現在先跑一次嗎？"; then
  launchctl kickstart -k "gui/$(id -u)/$LABEL"
  echo "約 2 分鐘後，在行事曆 App 的 iCloud 底下會出現 5 個行事曆。"
fi

# 13. 總結
echo ""
echo "安裝完成。"
echo "  每天自動更新：08:30、12:30、19:30、21:30（台北時間）"
echo "  日誌位置：$KIT/logs/"
echo "  移除方式：雙擊「移除-Mac.command」"
pause_exit 0
