# Punch Relay Discord Bot

這個 repository 是 Punch Relay Discord 打卡機器人的部署與維護包，內容包含 Discord bot 主程式、Windows 服務安裝腳本、內建 NSSM 執行檔、部署筆記，以及 GitHub 自動同步腳本。

新的 Windows 主機應該可以透過以下流程重建服務：clone repository、安裝 Python 依賴、建立本機 `.env`，再安裝 Windows 服務。

## 檔案說明

- `bot_all_in_one.py`: Discord bot 主程式。
- `requirements.txt`: Python 依賴清單。
- `.env.example`: 本機環境變數範本。複製成 `.env` 後在本機填入真實值。
- `tools/nssm/win32/nssm.exe` 和 `tools/nssm/win64/nssm.exe`: 內建 NSSM 執行檔。
- `install_nssm_service_admin.ps1`: 安裝或更新 `PunchBotService`。
- `restart_bot_admin.ps1`: 重新啟動 bot 服務。
- `restart_bot_resync_admin.ps1`: 刪除 `synced.flag` 並重新啟動 bot，讓 slash commands 重新同步。
- `sync_to_github.ps1`: commit 並 push 安全的 repository 變更。
- `watch_github_sync.ps1`: 監控資料夾變更，等變更穩定後執行同步腳本。
- `install_github_sync_watcher_admin.ps1`: 安裝 `PunchRelayGitSync` 監控用 Windows 服務。
- `agent.md`、`skill.md` 和 `docs/`: 維護與操作筆記。

## 不可上傳的本機檔案

下列檔案包含密鑰、個人資料、執行狀態或 log，已由 Git 忽略，不能上傳：

- `.env`
- `punch_data.json`
- `punched_today.json`
- `schedule_today.json`
- `admin_alerts_today.json`
- `synced.flag`
- `bot.log`
- `github_sync.log`
- `github_sync_watcher.log`
- `github_sync_service.log`
- `*.bak`
- `__pycache__/`

不要上傳 Discord token、GitHub token、e-HR 密碼、cookie、個人打卡資料或 log。

## 新主機安裝流程

1. Clone repository：

```powershell
git clone https://github.com/jeff210597/punch_relay.git
cd punch_relay
```

2. 安裝 Python 依賴：

```powershell
python -m pip install -r requirements.txt
```

3. 建立 `.env`：

```powershell
Copy-Item .env.example .env
notepad .env
```

填入本機設定值：

```text
DISCORD_TOKEN=your Discord bot token
NOTIFY_CHANNEL_ID=Discord notify channel ID
ADMIN_ALERT_CHANNEL_ID=Discord admin alert channel ID
EHR_BASE=http://your-ehr-host
```

`EHR_BASE` 結尾不要加 `/`。

4. 檢查 Python 語法：

```powershell
python -m py_compile bot_all_in_one.py
```

5. 用系統管理員 PowerShell 安裝或更新 bot 服務：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_nssm_service_admin.ps1
```

6. 指令有變更時，重新啟動並強制同步 slash commands：

```powershell
powershell -ExecutionPolicy Bypass -File .\restart_bot_resync_admin.ps1
```

一般重新啟動使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\restart_bot_admin.ps1
```

## GitHub 自動同步

PAT 只代表有 push 權限；資料夾變更偵測由另一個 watcher 服務負責。

1. 先確認主機上 GitHub HTTPS 驗證可用。互動式使用建議用 Git Credential Manager。Windows 服務若需要 push，安裝 watcher 時傳入 PAT，讓 LocalSystem 不需要互動式登入。不要把 PAT 存進 repository。

2. 手動測試安全同步：

```powershell
powershell -ExecutionPolicy Bypass -File .\sync_to_github.ps1 -DryRun
```

3. 用系統管理員 PowerShell 安裝 watcher：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_github_sync_watcher_admin.ps1
```

服務名稱是 `PunchRelayGitSync`。它會監控 repository 資料夾，並在變更穩定 45 秒後執行 `sync_to_github.ps1`。

若要給服務 push 權限，安裝時傳入本機 PAT：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_github_sync_watcher_admin.ps1 -GitHubPat "your PAT"
```

PAT 會存在本機 Windows 服務環境裡，不會寫入 Git 或 repository 檔案。

檢查服務與 log：

```powershell
Get-Service PunchRelayGitSync
Get-Content .\github_sync_watcher.log -Tail 50
Get-Content .\github_sync.log -Tail 50
```

## 驗證

```powershell
Get-Service PunchBotService
Get-Content .\bot.log -Tail 50 -Encoding UTF8
python -m py_compile bot_all_in_one.py
git status
```

每次 commit 或 push 前，都要確認 `.env`、runtime JSON、log、token、password 或備份檔沒有被 staged。
