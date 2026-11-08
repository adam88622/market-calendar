# market-calendar-ics

自動（每日 08:30／12:30／19:30／21:30）產生 5 個 `.ics` 行事曆檔，並（F-008）直接寫入 Apple 行事曆 App；也可手動匯入 .ics 看到台股結算、美股結算、台股法說與財報截止、美股 AI 供應鏈財報日、海外（美日韓）休市。

| 檔案 | 內容 |
|---|---|
| `output/tw-settlement.ics` | 台指期月選／週選結算日（遇休市依規則調整） |
| `output/us-settlement.ics` | 美股月選到期、四巫日、VIX 結算日 |
| `output/tw-earnings.ics` | 上市櫃法說會（MOPS 為主、Yahoo 補充）、季報／年報／月營收法定截止日 |
| `output/us-earnings.ics` | 觀察清單財報日（yfinance） |
| `output/global-holidays.ics` | 美股（NYSE）休市與提早收盤、日股（東證）休市、韓股（KRX）休市 |

## 安裝

需要 Python 3.11 以上與 [uv](https://github.com/astral-sh/uv)。

```bash
cd market-calendar-kit
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt
scripts/install.sh        # 安裝 launchd 每日 08:30／12:30／19:30／21:30 排程
```

移除排程：`scripts/uninstall.sh`。

## 執行

手動執行一次：

```bash
.venv/bin/python main.py
```

手動觸發 launchd 任務：

```bash
launchctl kickstart -k gui/$(id -u)/com.market-calendar-ics.daily
```

結果會寫到 `output/`，日誌在 `logs/run-YYYYMMDD.log`。結束碼：0 全部成功、1 有行事曆失敗（保留舊檔）或 Apple 行事曆同步失敗／未授權、2 設定檔錯誤。

參數：`--authorize`（授權，見下節）、`--config PATH`（指定設定檔）。

## 直接寫入 Apple 行事曆（F-008）

每天產生 .ics 後，程式會自動把同一批事件同步到 Apple 行事曆 App 的 5 個行事曆（預設建在 iCloud 帳號下，iPhone 會自動同步）。啟用後**不需要再手動匯入 .ics**；.ics 仍照常產出，作為備份和手動匯入用。

### 第一次設定（授權）

背景排程以 `scheduler/MarketCalendarSync.app` 身分執行，授權也要由這個 app 觸發：

```bash
cd market-calendar-kit
open "$PWD/scheduler/MarketCalendarSync.app" --args --authorize
```

在跳出的視窗按「允許完整取用」。結果會寫入 `logs/authorize-YYYYMMDD-HHMMSS.log`，查看：

```bash
cat logs/authorize-*.log | tail
```

也可以在 Terminal 以終端機身分授權（供手動執行 `main.py` 用）：`.venv/bin/python main.py --authorize`。被拒絕時，到「系統設定 → 隱私權與安全性 → 行事曆」開啟權限。

重新安裝或搬移專案後，若日誌中 `authorization status` 不是 fullAccess，請重新執行上面的 `open ... --args --authorize`。

### 以前用 .ics 手動匯入過的話

先在行事曆 App 刪掉以前匯入的同名行事曆（台股結算、美股結算等），否則會出現兩個同名行事曆，事件也會重複。程式不會接手不是它建立的行事曆。

### 同步規則

- 以 UID 為鍵：新增、內容有變就更新、沒變就不動。
- 今天以後、已經從來源消失的事件（取消或改期）會自動刪除；今天以前的事件不刪。
- 只動程式自己建立的 5 個行事曆裡、帶有 `market-calendar-ics://` 網址的事件。使用者自己加進這些行事曆的事件、以及其他行事曆，都不會被動到。
- 某個 .ics 產生失敗時，該行事曆本次不同步（避免誤刪），其他照常。
- 有時間預算（約 105 秒）。第一次大量建立若來不及完成，剩下的會延到下次執行自動補完（日誌 `deferred`）；也可以在 Terminal 手動多跑一次 `main.py`。

### 注意事項

- 在 App 裡手動修改本專案的事件，下次來源內容變更時會被覆蓋。
- 手動刪除本專案的事件，下次會被重建；如果不想要某類事件，請改 config。
- 刪除整個行事曆，下次會重建。這也是「重置」的方法。

### iCloud 與本機

預設 `apple_calendar.prefer_source: iCloud`。找不到 iCloud（Mac 沒登入或沒開啟 iCloud 行事曆）時，會建在「我的 Mac」，日誌有 WARNING，iPhone 看不到。可改成 `local`。

### 刪除防護

一次要刪超過一半（且至少 5 筆）的未來事件時，程式會暫停刪除並記 WARNING。確認無誤後，在 App 刪除該行事曆，讓程式重建。

### 排程（launchd）與權限

- 執行時間由 `config.yaml` 的 `schedule.times` 決定，預設每日 08:30、12:30、19:30、21:30（`times` 優先於 `hour`/`minute`）；修改後重跑 `scripts/install.sh` 生效。
- 離線跳過：每次執行先依 `network_check`（`enabled`、`timeout_sec`）連 MOPS 與 Yahoo，全部連不上就記 INFO `offline: network check failed; skipped this run ...`，不更新任何檔案、資料庫與 Apple 行事曆，結束碼 0，等下一個時間點。
- 睡眠中錯過的時間點，喚醒後 launchd 會補跑一次；關機則不補跑，等下一個時間點。

- 背景排程的權限歸屬可能和 Terminal 不同；`scripts/install.sh` 會建立 `.app` 啟動器並提示授權指令。
- 檢查方式：`grep "authorization status" logs/run-$(date +%Y%m%d).log`。不是 fullAccess 時，F-008 會被跳過（.ics 不受影響），結束碼 1。
- `schedule.launch_mode` 可選 `direct`（預設）或 `open`；處理方式和備案請參考 開發文件（未隨安裝包附上） 附錄 v4 V4-6 與 v4.1 R10。

### 關閉或移除

- 設 `apple_calendar.enabled: false` 就會停止寫入（也不會載入 EventKit）。
- 已經建立的行事曆不會自動刪除，需要手動在 App 刪除。
- 想清理對應表的話，刪除 `data/calendar.db` 中的 `apple_*` 兩張表即可（下次會用 URL 自動重新接上）。

## 海外休市行事曆（F-009）

行事曆「海外休市（美日韓）」（`output/global-holidays.ics`）列出週一到週五的海外休市日：

- 美股休市：「美股休市：感恩節」等；美股提早收盤：「美股提早收盤（13:00 ET）：感恩節翌日」，說明欄附台北時間（例如 13:00 ET ＝ 台北隔天 02:00，夏令時間為 01:00）。
- 日股休市：「日股休市：文化の日」等（日股節日名稱用日文原名）。
- 韓股休市：「韓股休市：中秋（秋夕）」等。
- 同一天多個市場休市，各自一筆；週末不列。

資料來源：休市與提早收盤時間以 `pandas_market_calendars`（XNYS、JPX、XKRX）為準；節日名稱來自 `pandas_market_calendars`（美股）和 `holidays` 套件（日、韓），取不到時顯示「休市」。**未來年度為依規則推算，以交易所公告為準**；臨時休市（颱風、國喪等）可能要等套件更新後才會出現，可以執行 `uv pip install -U pandas_market_calendars holidays` 更新。

- 已啟用 F-008 時，升級後第一次執行會在 iCloud 自動建立第 5 個行事曆，不需要重新授權或重新安裝排程。
- `config.yaml` 的 `calendars.global-holidays` 可以改名稱和檔名；沒有寫這一項時用預設值（`global-holidays.ics`、「海外休市（美日韓）」）。
- 名稱套件無法使用時，事件仍然照常產出（名稱退化為「休市」），結束碼為 1，日誌有 `global-holidays:degraded`。
- 離線時整次執行跳過，global-holidays 也不會更新（下次連網時補上）。
- 資料涵蓋年限：`holidays` 套件（>=0.108）的韓股農曆節日（XKRX，如設日、秋夕）清單只到 2029 年；超過後需升級套件（`uv pip install -U holidays pandas_market_calendars`）。資料涵蓋防護：範圍內某年份若某市場完全沒有休市日，該次 global-holidays 會失敗並保留舊檔，避免誤刪 iCloud 既有事件。
- 時間預算吃緊時 global-holidays 排最後，最先延後，下一次排程補上。

## 手動匯入 Apple 行事曆（不啟用 F-008 時）

1. 開啟「行事曆」App，為每個檔案各自新建一個行事曆（例如「台股結算」「美股結算」「台股法說與財報」「美股財報」「海外休市（美日韓）」），避免混在一起。
2. 對 5 個 `.ics` 檔，各自選「檔案 → 匯入…」，並在對話框中選擇各自對應的行事曆。
3. 每天更新後，把新的檔案重新匯入**同一個**行事曆。事件以 UID 辨識，內容沒變的事件不會重複，改動過的事件會被更新。提醒為事件前一天 20:00（台北時間）。

### 重要：手動匯入不會自動刪除舊事件

**此段只適用於手動匯入 .ics 的情況**；啟用 F-008 後，由程式自動刪除已消失的未來事件。

Apple 行事曆依 UID 覆蓋更新，但**匯入不會刪除新檔中已不存在的舊事件**。例如法說會改期後的舊日期、yfinance 舊的預估日、已取消的事件，都會留在行事曆裡。遇到這種情況請手動刪除該事件，或刪除整個行事曆後重新匯入。

## 設定

所有設定在 `config.yaml`，修改後下次執行生效。

- **美股觀察清單**：修改 `us_earnings.watchlist`（每檔 `{ticker, group}`，ticker 不可重複）。
- **休市表**：`taifex_holidays` 需**每年更新**。2026 年依公告核對；**2027 年為暫定值**（每筆標 `provisional: true`），期交所公告後請更正，並補入春節前僅結算交割無交易的日子（`kind: market_only`）。臨時休市（颱風假等）可寫在 `taifex_extra_holidays`。
- 週選遇休市的處理：`tw_settlement.weekly_*.holiday_policy`（next / previous / skip）。

## 資料來源與限制

- 台股法說：公開資訊觀測站 MOPS（偶爾回 502，程式會重試）、Yahoo 股市。
- 美股財報：**yfinance 為非官方來源**，可能改版、被限流或日期為預估值（說明欄會標示）。經常被擋時，把 `us_earnings.max_workers` 降到 4，或 `submit_stagger_sec` 調到 0.5。
- 海外休市資料來自 pandas_market_calendars 和 holidays 套件，屬離線規則推算，以交易所公告為準。
- 網路來源失敗時，會以本機累積庫（`data/calendar.db`）照常產出。

## 分享給朋友與雲端訂閱（F-010）

- 非 macOS 執行時，Apple 行事曆同步自動停用（日誌 INFO），.ics 照常產出。
- 雲端版：GitHub Actions 每日 4 次（台北 08:30／12:30／19:30／21:30）用 `config.cloud.yaml` 產生 5 個 .ics，發布到 GitHub Pages；累積資料存在 `state` 分支（build job 只讀，由 save-state job 推送）。首頁 `index.html` 與 `status.json` 顯示每個行事曆最後成功更新時間與各資料來源狀態；失敗的行事曆沿用上一版。
- 沒有 health／alert 機制：job 失敗時由 GitHub 內建的失敗 email 通知（寄給最後修改 workflow 的人）。
- 雲端日誌：Actions 的 `run-logs-<run id>` artifact（保留 14 天），內含 `cloud-logs/run-YYYYMMDD.log`、`run-report.json`、`restore-info.json`。
- 產生分享安裝包：`.venv/bin/python tools/export_kit.py` → `dist/market-calendar-kit/` 與 `.zip`（會掃描個人路徑與 email，未通過不產出）。
- 說明文件：`你要做的-雲端設定步驟.md`、`給朋友-訂閱教學.md`、`安裝-Mac說明.md`。
- `main.py --report PATH`：寫出本次執行的結構化報告（JSON），雲端版用來產生 status.json。
- `config.cloud.yaml` 除 paths／network_check／apple_calendar 外須與 `config.yaml` 相同；改觀察清單時兩檔一起改。

## 測試

```bash
.venv/bin/python -m pytest tests -m "not live and not eventkit"   # 離線（預設）
.venv/bin/python -m pytest tests -m live          # 需要網路（完整 run 改用停用 apple 的暫存 config）
MCI_EVENTKIT_TESTS=1 .venv/bin/python -m pytest tests -m eventkit   # 實機 EventKit，需已授權；只動暫時行事曆
```
