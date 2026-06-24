# Punch Relay Discord Bot

Windows 上的 Discord 自動打卡機器人。正式執行使用 NSSM Windows 服務，因此開機後不必登入 `7c`；日常查詢與重啟不需要管理員權限。

## 首次移轉

1. 使用專案內 `.python` 可攜式 Python 3.13，或以目前使用者安裝 64-bit Python 3.13。
2. 執行 `setup_python_env.ps1` 建立 `.venv` 並安裝依賴。
3. 確認 `.env` 具有 `DISCORD_TOKEN`、`NOTIFY_CHANNEL_ID`、`ADMIN_ALERT_CHANNEL_ID` 與 `EHR_BASE`。
4. 僅此一次，以系統管理員 PowerShell 執行 `install_nssm_service_admin.ps1`。

安裝腳本會將服務設為開機自動啟動、異常退出 10 秒後重啟，並只授予 `7c` 控制 `PunchBotService` 的權限。

## 日常操作（不需管理員）

```powershell
# 查看狀態與近期日誌
powershell -ExecutionPolicy Bypass -File .\bot_status.ps1

# 一般重啟
powershell -ExecutionPolicy Bypass -File .\restart_bot.ps1

# 修改 slash commands 後重新同步
powershell -ExecutionPolicy Bypass -File .\restart_bot_resync.ps1
```

`start.bat` 等同一般重啟。腳本只控制 `PunchBotService`，不會終止電腦上其他 Python 程序。

## 自動復原

- Discord 連線中斷超過 `DISCONNECT_RESTART_SECONDS`（預設 300 秒）時，Bot 退出並由 NSSM 重啟。
- 關機、斷電或睡眠期間，本機無法發送告警；外部心跳方案目前不啟用。

睡眠中的電腦無法靠本機程式自行喚醒。建議插電與電池模式都設為不自動睡眠。

## e-HR 紀錄判定

所有狀態畫面與自動比對統一使用 B9 考勤彙總表：

- index 5（刷卡）為正式上班時間。
- index 6（出卡）為正式下班／值班下班時間。
- 原始刷卡與補刷卡只在 index 5／6 尚未回填時作為輔助推算。
- Bot 本機 `punched_today.json` 只表示程式已送出打卡，不等同 e-HR 已記錄；畫面會明確區分兩者。

這套判定共用於 `/查今日狀態`、`/查詢本日e-hr刷卡記錄`、`/管理 帳號`、`/管理 今日打卡驗證`、打卡後確認，以及 18:00／09:00 自動比對。

## 主要檔案

- `bot_all_in_one.py`：Discord Bot、打卡、B9 查詢與排程。
- `install_nssm_service_admin.ps1`：唯一需要管理員權限的一次性服務安裝。
- `restart_bot.ps1`、`restart_bot_resync.ps1`、`bot_status.ps1`：一般使用者日常維護。
- `setup_python_env.ps1`：建立專案 Python 環境。
- `set_discord_token.ps1`：只更新本機 `.env` 中的 Token，不輸出 Token。

## 權限

管理員權限只用於首次安裝、重新設定或移除 Windows 服務。Python 套件、日誌、日常重啟及 Discord 指令同步均使用一般使用者權限。

## 本機敏感資料

`.env`、JSON 執行資料、`synced.flag`、日誌、備份、`.python` 與 `.venv` 不應上傳。GitHub 自動 commit/push 與 watcher 已從此專案移除；`.git` 僅保留供日後手動版本控制。
