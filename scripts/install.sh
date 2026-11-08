#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PRJ="$(pwd)"
.venv/bin/python -m scheduler.launchd install
echo "手動觸發：launchctl kickstart -k gui/$(id -u)/com.market-calendar-ics.daily"
if [ -d "$PRJ/scheduler/MarketCalendarSync.app" ]; then
  echo "Apple 行事曆已啟用，請執行一次授權（由 app 身分觸發授權視窗）："
  echo "  open \"$PRJ/scheduler/MarketCalendarSync.app\" --args --authorize"
fi
