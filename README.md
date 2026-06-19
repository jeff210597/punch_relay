# Punch Relay Discord Bot

這個 repository 包含 Discord 打卡機器人的程式、Windows 服務安裝腳本、NSSM 執行檔與部署文件。新主機只要 clone GitHub repo、安裝 Python 套件、建立 `.env`，即可重現目前的運行環境。

## 專案檔案

- `bot_all_in_one.py`：Discord bot 主程式。
- `requirements.txt`：Python 套件清單。
- `.env.example`：本機 `.env` 範本，請複製後填入真實 token、頻道 ID 與 e-HR 網址。
- `tools/nssm/win32/nssm.exe`、`tools/nssm/win64/nssm.exe`：已隨 repo 附上的 NSSM，依 Windows 架構自動選用。
- `install_nssm_service_admin.ps1`：以系統管理員權限安裝或更新 `PunchBotService`。
- `restart_bot_admin.ps1`：重啟 bot，不強制重新同步 slash command。
- `restart_bot_resync_admin.ps1`：刪除 `synced.flag` 後重啟 bot，讓 slash command 重新同步。
- `start.bat`：互動式管理員啟動器，可選擇是否重新同步 slash command。
- `agent.md`、`skill.md`、`docs/`：Codex 與維護文件。

## 不上傳 GitHub 的本機資料

以下檔案包含機密、個人設定或執行狀態，會被 `.gitignore` 排除：

- `.env`
- `punch_data.json`
- `punched_today.json`
- `schedule_today.json`
- `admin_alerts_today.json`
- `synced.flag`
- `bot.log`
- `*.bak`
- `__pycache__/`

不要把 Discord token、e-HR 密碼、cookie、個人打卡資料或 log 上傳到 GitHub。

## 新主機部署

1. Clone repo 到任意資料夾，例如：

```powershell
git clone https://github.com/jeff210597/punch_relay.git
cd punch_relay
```

2. 安裝 Python 3.11 以上，並安裝套件：

```powershell
python -m pip install -r requirements.txt
```

3. 建立 `.env`：

```powershell
Copy-Item .env.example .env
notepad .env
```

填入：

```text
DISCORD_TOKEN=你的 Discord bot token
NOTIFY_CHANNEL_ID=通知頻道 ID
EHR_BASE=http://你的-ehr-host/
ADMIN_ALERT_CHANNEL_ID=管理員告警頻道 ID
```

`ADMIN_ALERT_CHANNEL_ID` 可留空或使用預設值；若頻道無法送出，程式會 fallback 到 `ADMIN_IDS`。

4. 語法檢查：

```powershell
python -m py_compile bot_all_in_one.py
```

5. 以系統管理員權限安裝或更新 Windows 服務：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_nssm_service_admin.ps1
```

這個腳本會使用腳本所在資料夾作為服務工作目錄，不要求 repo 一定放在特定路徑。

6. 需要重新同步 slash command 時：

```powershell
powershell -ExecutionPolicy Bypass -File .\restart_bot_resync_admin.ps1
```

一般重啟：

```powershell
powershell -ExecutionPolicy Bypass -File .\restart_bot_admin.ps1
```

## 驗證服務

```powershell
Get-Service PunchBotService
Get-Content .\bot.log -Tail 50 -Encoding UTF8
```

服務應為 `Running`。若剛新增或修改 slash command，請使用 `restart_bot_resync_admin.ps1` 重新啟動。

## NSSM 架構選擇

`start.bat` 與 `install_nssm_service_admin.ps1` 都會依目前 Windows 架構選擇 NSSM：

- 64-bit Windows：`tools\nssm\win64\nssm.exe`
- 32-bit Windows：`tools\nssm\win32\nssm.exe`

## 維護流程

修改程式後至少執行：

```powershell
python -m py_compile bot_all_in_one.py
git status
git diff
```

提交前再次確認沒有 staged `.env`、log、json 狀態檔、token 或密碼。
