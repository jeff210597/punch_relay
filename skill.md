# Punch Relay 操作摘要

## 啟動架構

- `.venv\Scripts\python.exe bot_all_in_one.py` 由 NSSM 的 `PunchBotService` 執行。
- 服務開機自動啟動，異常退出後延遲重啟。
- `7c` 只具備這個服務的查詢、啟動與停止權限；日常不使用管理員。

## 驗證命令

```powershell
.\.venv\Scripts\python.exe -m py_compile .\bot_all_in_one.py
powershell -ExecutionPolicy Bypass -File .\bot_status.ps1
powershell -ExecutionPolicy Bypass -File .\restart_bot.ps1 -NoPause
```

slash commands 改變時才執行：

```powershell
powershell -ExecutionPolicy Bypass -File .\restart_bot_resync.ps1 -NoPause
```

## 安全限制

- 不輸出或上傳 `.env`、員工資料、密碼、token、cookie、日誌或 runtime JSON。
- 不可終止其他 Python 程序。
- 不建立 GitHub watcher、自動 commit 或自動 push。
