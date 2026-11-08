#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m scheduler.launchd uninstall
echo "手動觸發（若重新安裝後）：launchctl kickstart -k gui/$(id -u)/com.market-calendar-ics.daily"
