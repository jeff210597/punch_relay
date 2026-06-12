# Punch Relay 代理維護指南

## 角色定位

這個檔案是給維護本 repository 的代理使用，重點是定義如何修改、驗證、重啟與發布打卡機器人。它不負責完整描述打卡業務規則；打卡流程、補打、重試、告警判斷請看 `skill.md`。

主要職責：

- 保護 Discord token、e-HR 帳密、員工資料、log 與本機狀態檔。
- 維持打卡行為保守：不能重複打卡、不能漏掉符合條件的打卡、不能在 08:00 或之後自動補打上班卡。
- 每次修改程式、設定範例或文件後，都要驗證；必要時重啟服務、檢查 log、commit，並 push 到 GitHub。

## 專案檔案

- `bot_all_in_one.py`：主要 Discord bot 程式。
- `agent.md`：本 repository 的代理維護規則。
- `skill.md`：打卡流程與業務判斷規則。
- `.env.example`：安全的環境變數範例。
- `.env`：本機秘密設定，禁止提交。
- `punch_data.json`：綁定使用者資料，禁止提交。
- `punched_today.json`：當日已成功打卡紀錄，禁止提交。
- `schedule_today.json`：當日隨機排程時間，禁止提交。
- `admin_alerts_today.json`：當日管理員告警去重紀錄，禁止提交。
- `bot.log`：執行 log，禁止提交。

## 目前執行設定

環境變數：

- `DISCORD_TOKEN`：必填，Discord bot token。
- `NOTIFY_CHANNEL_ID`：必填，備用通知頻道。
- `ADMIN_ALERT_CHANNEL_ID`：管理員異常告警頻道，目前是 `1514929880448630904` (`admin-alert`)。
- `EHR_BASE`：必填，e-HR 基礎網址。

打卡時段：

- 上班 `in`：07:00-07:40
- 一般下班 `out`：17:05-17:40
- 值班隔日下班 `dutyout`：值班隔日 08:05-08:40

重試與補打：

- 自動重試間隔：2 分鐘。
- 自動重試上限：最多 3 次。
- 補打按鈕有效時間：10 分鐘。
- 上班 `in` 的自動重試與補打截止：必須嚴格早於 08:00。

## Agent 與 Skill 的分工

Agent 負責：

- 閱讀程式碼與本機文件。
- 修改這個 repository 內的檔案。
- 執行驗證。
- 必要時重啟 `PunchBotService`。
- 檢查 log。
- 掃描是否誤放秘密資料。
- commit 並 push 到 GitHub。

Skill 負責：

- 判斷是否應該打卡。
- 說明重試、補打按鈕、e-HR 比對、請假、值班、週末、取消日期與管理員告警規則。
- 在修改打卡邏輯時，避免違反業務規則。

請避免在這裡重複描述太細的打卡流程。具體打卡判斷以 `skill.md` 為準。

## 修改規則

1. 修改會影響執行行為的內容前，先確認現在是否接近打卡時段。
2. 優先做小範圍 patch，不做無關重構。
3. 維持 `punch_data.json` 向後相容；新增欄位時透過 `get_user_data()` 補預設值。
4. 只有在 bot 角度確認成功後，才能寫入 `punched_today.json`。
5. 絕不能讓自動重試或補打按鈕在 08:00 或之後呼叫 `punch_clock(..., "in")`。
6. 長時間 HTTP 工作不要卡住 Discord event loop。
7. 不要印出或寫進文件：token、密碼、cookie、原始 e-HR payload、完整敏感 URL。
8. 若新增、移除或改名 slash command，要用 resync script 重啟。

## 驗證

每次都要執行：

```powershell
python -m py_compile bot_all_in_one.py
```

若修改打卡行為，也要確認：

- `punched_today.json` 的 key 能防止重複打卡。
- `schedule_today.json` 會保留同一天既有的隨機時間。
- `retry_queue` 會在 2 分鐘後重試，並在 3 次後停止。
- 上班 `in` 的補打與重試，在排程當下、重試執行當下、按鈕確認當下，都有 08:00 截止檢查。
- 管理員告警會送到 `admin-alert`，不是 `#一般`。

上傳 GitHub 前，要掃描是否有敏感內容：

```powershell
rg -n "DISCORD_TOKEN|EHR_BASE|pwd|password|token|密碼" README.md docs bot_all_in_one.py restart_bot_admin.ps1 restart_bot_resync_admin.ps1 .env.example agent.md skill.md
```

只出現變數名稱或安全範例可以接受。真實 token、密碼、員工編號、內部網址、cookie 或 log 不可以提交。

## 重啟

一般重啟：

```powershell
Start-Process -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File C:\punch_relay\restart_bot_admin.ps1 -NoPause" -Verb RunAs -WindowStyle Hidden
```

需要同步 slash command 時重啟：

```powershell
Start-Process -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File C:\punch_relay\restart_bot_resync_admin.ps1 -NoPause" -Verb RunAs -WindowStyle Hidden
```

重啟後確認：

- `PunchBotService` 狀態是 `Running`。
- `bot.log` 有 bot 啟動紀錄。
- `auto_punch_task` 已開始。

## GitHub 規則

每次完成程式、設定範例、文件、agent 規則或 skill 規則的修改後，都必須上傳到 GitHub。

使用：

```powershell
git -c safe.directory=C:/punch_relay status --short
git -c safe.directory=C:/punch_relay diff
git -c safe.directory=C:/punch_relay add <changed-files>
git -c safe.directory=C:/punch_relay commit -m "<clear message>"
git -c safe.directory=C:/punch_relay push origin main
```

commit 前要確認這些敏感或本機狀態檔沒有被 staged：

- `.env`
- `punch_data.json`
- `punched_today.json`
- `schedule_today.json`
- `admin_alerts_today.json`
- `bot.log`
- `.codex-remote-attachments/`

如果 push 失敗，不要 force push、reset、rebase 或修改 remote。回報錯誤並等待使用者確認。
