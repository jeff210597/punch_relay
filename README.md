# 公司外打卡 Discord Bot

這是公司內部使用的 e-HR 公司外打卡 Discord 機器人。

## 主要檔案

- `bot_all_in_one.py`：主程式。
- `start.bat`：Windows service 重啟腳本。
- `agent.md`：給後續開發代理使用的維護指南。
- `skill.md`：打卡流程判斷與補打/告警規則 skill。
- `docs/codex/vscode_tunnel_mobile_guide.md`：手機使用 VS Code Tunnel 遠端修改教學。
- `docs/github/git_diff.md`：Git diff 說明。
- `docs/github/github_upload_guide.md`：GitHub 安全上傳教學。
- `.gitignore`：避免敏感資料與執行紀錄被提交到 GitHub。
- `.env.example`：環境變數範例。

## 檔案分類

### 打卡機器人啟動與執行相關

這些檔案會影響 bot 啟動、服務重啟或每日打卡狀態，建議保留在 `C:\punch_relay` 根目錄：

- `bot_all_in_one.py`
- `start.bat`
- `restart_bot_admin.ps1`
- `restart_bot_resync_admin.ps1`
- `nssm.exe`
- `requirements.txt`
- `.env.example`
- `punch_data.json`
- `punched_today.json`
- `schedule_today.json`
- `admin_alerts_today.json`
- `synced.flag`
- `bot.log`

### Codex / 文件 / GitHub 維護相關

這些檔案用於後續維護、遠端修改與 GitHub 上傳教學：

- `README.md`
- `.gitignore`
- `agent.md`
- `skill.md`
- `docs/codex/vscode_tunnel_mobile_guide.md`
- `docs/github/git_diff.md`
- `docs/github/github_upload_guide.md`

## 不應上傳到 GitHub 的檔案

以下檔案可能包含密碼、使用者資料、執行紀錄或本機狀態，已列入 `.gitignore`：

- `punch_data.json`
- `punched_today.json`
- `schedule_today.json`
- `admin_alerts_today.json`
- `bot.log`
- `synced.flag`
- `.env`
- `nssm.exe`

## 啟動前環境變數

建議將敏感設定放在 Windows 環境變數，不要寫死在程式碼中：

```powershell
setx DISCORD_TOKEN "你的 Discord bot token"
setx NOTIFY_CHANNEL_ID "通知頻道 ID"
setx ADMIN_ALERT_CHANNEL_ID "管理員異常告警私人頻道 ID"
setx EHR_BASE "e-HR 系統網址"
```

`ADMIN_ALERT_CHANNEL_ID` 為選填。若有設定，打卡失敗、重試失敗、e-HR 最終缺卡等管理員告警會優先發到該私人頻道；若未設定或 Bot 無法發送，會 fallback 私訊 `ADMIN_IDS` 管理員。

設定後請重新開啟終端機或重啟 Windows service。

## Codex 修改與重啟

後續可以在 Codex 內直接修改 `bot_all_in_one.py`。建議流程：

1. 修改程式。
2. 執行 Python 語法檢查。
3. 一般修改：重啟 bot，但不重新同步 Discord 指令。
4. 若新增、刪除或改名 slash command：重啟時重新同步 Discord 指令。
5. 重啟後查看 `bot.log`，確認 bot 正常啟動。

注意：重啟 `PunchBotService` 通常需要系統管理員權限，Codex 執行重啟前需要使用者授權。

### 非互動重啟腳本

一般重啟，不重新同步 Discord 指令：

```powershell
powershell -ExecutionPolicy Bypass -File C:\punch_relay\restart_bot_admin.ps1
```

新增、刪除或改名 slash command 後使用，會刪除 `synced.flag` 並重新同步：

```powershell
powershell -ExecutionPolicy Bypass -File C:\punch_relay\restart_bot_resync_admin.ps1
```

## GitHub 上傳提醒

上傳 GitHub 前請先執行：

```powershell
git status
git diff
```

確認沒有密碼、token、員工資料或 log 被加入提交。
