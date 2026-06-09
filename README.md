# 公司外打卡 Discord Bot

這是公司內部使用的 e-HR 公司外打卡 Discord 機器人。

## 主要檔案

- `bot_all_in_one.py`：主程式。
- `start.bat`：Windows service 重啟腳本。
- `agent.md`：給後續開發代理使用的維護指南。
- `git_diff.md`：Git diff 說明。
- `.gitignore`：避免敏感資料與執行紀錄被提交到 GitHub。
- `.env.example`：環境變數範例。

## 不應上傳到 GitHub 的檔案

以下檔案可能包含密碼、使用者資料、執行紀錄或本機狀態，已列入 `.gitignore`：

- `punch_data.json`
- `punched_today.json`
- `schedule_today.json`
- `bot.log`
- `synced.flag`
- `.env`
- `nssm.exe`

## 啟動前環境變數

建議將敏感設定放在 Windows 環境變數，不要寫死在程式碼中：

```powershell
setx DISCORD_TOKEN "你的 Discord bot token"
setx NOTIFY_CHANNEL_ID "通知頻道 ID"
setx EHR_BASE "e-HR 系統網址"
```

設定後請重新開啟終端機或重啟 Windows service。

## GitHub 上傳提醒

上傳 GitHub 前請先執行：

```powershell
git status
git diff
```

確認沒有密碼、token、員工資料或 log 被加入提交。
