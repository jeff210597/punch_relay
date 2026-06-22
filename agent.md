# Punch Relay 維護規則

- 正式程序由 `PunchBotService`（NSSM）執行。
- 首次安裝或變更 Windows 服務時才使用管理員權限。
- 日常狀態、重啟及 Discord 指令同步使用 `bot_status.ps1`、`restart_bot.ps1`、`restart_bot_resync.ps1`，不得要求 UAC。
- 不得用程序名稱批次終止所有 Python，只能控制 `PunchBotService`。
- `.env`、使用者 JSON、日誌、`synced.flag`、`.venv` 與任何 token/密碼不得提交。
- GitHub 自動同步已停用；不得建立 watcher、自動 commit 或自動 push。Git 僅能由使用者明確要求時手動操作。
- 修改後至少執行 Python 語法檢查並查看 `bot.log`；服務相關改動另驗證 NSSM 狀態及非管理員重啟。
