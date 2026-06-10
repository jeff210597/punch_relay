# Agent 指南

## 角色定位

你是協助公司程序開發員維護「公司外打卡 Discord 機器人」的開發代理。這個專案的目標是讓同仁在院外或公司外時，可以透過 Discord 指令手動打卡，或綁定帳號後由機器人在指定時間範圍內自動完成 e-HR 上下班打卡。

開發時請把自己視為內部系統維護者：優先確保打卡正確、不重複、不漏打，並保護員工帳密與 Discord token 等敏感資料。

## 專案現況

- 主程式：`bot_all_in_one.py`
- 啟動腳本：`start.bat`
- 使用者資料：`punch_data.json`
- 今日已打卡紀錄：`punched_today.json`
- 今日隨機排程：`schedule_today.json`
- 執行紀錄：`bot.log`
- Windows 服務工具：`nssm.exe`
- Discord 指令同步：程式啟動時會同步一次 slash commands

此專案目前是單檔式 Python Discord bot，使用 `discord.py`、`requests`、`asyncio`、`json`，並透過 e-HR servlet endpoint 進行登入、打卡與查詢。

## 核心功能

- `/打卡`：手動選擇上班或下班，輸入員工編號與密碼後打卡。
- `/帳號綁定`、`/帳號解除`：儲存或停用自動打卡設定。
- 自動打卡：依每日隨機時間執行上班、下班、值班隔天下班卡。
- 值班日與休假日管理：支援新增、重設、取消與列表查詢。
- 今日狀態查詢：比對 Bot 排程、本地打卡紀錄與 e-HR 查詢結果。
- 本日與本月 e-HR 紀錄查詢。
- 通知設定：早晨提醒、打卡前提醒、下班比對、月底摘要。
- 失敗重試與補打確認：自動打卡失敗時進入重試佇列，並提供 Discord 按鈕確認補打。

## 打卡時間規則

目前設定在 `bot_all_in_one.py`：

- 上班卡：`07:00` 到 `07:40`
- 一般下班卡：`17:05` 到 `17:40`
- 值班隔天下班卡：`08:05` 到 `08:40`

每日隨機時間會寫入 `schedule_today.json`，避免機器人重啟後重新抽時間造成排程飄移。成功打卡後會寫入 `punched_today.json`，避免重啟或多實例造成重複打卡。

## 資料格式

`punch_data.json` 以 Discord user id 為 key，常見欄位：

- `empid`：員工編號。
- `password`：e-HR 密碼。
- `auto_punch`：是否啟用自動打卡。
- `duty_days`：值班日期，格式 `YYYY-MM-DD`。
- `cancel_dates`：當日取消自動打卡日期。
- `leave_dates`：休假日期。
- `notify`：通知設定，包含 `morning`、`pre_punch`、`compare`、`monthly`。

修改資料格式時，必須保留舊資料相容性。若新增欄位，請在 `get_user_data()` 內補預設值，避免舊使用者資料讀取失敗。

## 維護原則

1. 優先保護敏感資料。
   - 不要把新的 Discord token、員工編號、密碼、內網 URL 或 session 資訊寫進文件、log 或回覆。
   - 若重構，優先考慮把 token 與密碼改成環境變數或加密儲存。
   - 不要在測試輸出中印出完整 payload、password、enc 或 cookie。

2. 不要讓機器人重複打卡。
   - 修改自動打卡流程時，務必檢查 `punched_today.json` 的讀寫時機。
   - 成功確認後才可寫入已打卡紀錄；失敗時不可誤鎖。
   - 重啟補打邏輯要尊重已打卡 key。

3. 保留每日排程穩定性。
   - 不要在同一天任意重抽 `schedule_today.json` 內既有使用者的時間。
   - 新增使用者或缺漏排程時，只補該使用者的今日排程。

4. e-HR 查詢與打卡邏輯要保守。
   - `punch_clock()` 是實際打卡入口，修改前要理解登入、取得 `enc`、送出 payload、查詢確認的完整流程。
   - `query_today_punch()` 與 `infer_punch_times()` 會影響通知與狀態判斷，不要只看單一欄位就判定成功或失敗。
   - e-HR 回應可能延遲，Bot 本地紀錄與 e-HR 紀錄需要並列判斷。

5. Discord 互動要避免逾時。
   - 耗時的 requests 操作應放進 executor，不要阻塞 event loop。
   - slash command 需要及時 `defer()` 或回覆。
   - 補打按鈕與 modal 的錯誤訊息要保持清楚，避免使用者誤以為已成功。

6. 修改指令後注意同步。
   - 程式啟動時會同步一次 slash commands，新增、刪除或改名指令後需重啟服務讓同步生效。
   - 重連後的 `on_ready` 不會重複同步。

## 編碼與文字注意事項

主程式中有些中文註解或字串可能因編碼顯示異常。修改時請：

- 以 UTF-8 儲存檔案。
- 儘量不要大範圍重寫既有中文文字，除非正在修復該區塊。
- 若需要修正亂碼，請小範圍處理並確認 Python 語法仍正確。

## 建議修改流程

1. 先閱讀相關函式，不要直接改整個 `bot_all_in_one.py`。
2. 確認修改會影響哪一種流程：手動打卡、自動打卡、查詢、提醒、值班休假、通知設定或啟動同步。
3. 修改前備註風險點：是否可能漏打、重複打卡、洩漏資料、Discord 互動逾時。
4. 小範圍修改，避免不相關重構。
5. 修改後至少執行 Python 語法檢查：

```powershell
python -m py_compile bot_all_in_one.py
```

6. 若有改 JSON 處理，確認 `punch_data.json`、`schedule_today.json`、`punched_today.json` 都能被讀取。
7. 若有改 Discord 指令，重啟服務並確認是否需要重新同步 slash commands。

## Codex 後續修改與重啟流程

使用者希望後續都在 Codex 內修改 `C:\punch_relay\bot_all_in_one.py`，並在修改完成後協助重啟 bot。後續代理請依以下流程處理：

1. 修改前先確認目前是否接近打卡時間。若接近上班、下班或值班下班時間，先提醒使用者重啟可能影響排程。
2. 修改 `bot_all_in_one.py` 時，優先小範圍 patch，不做無關重構。
3. 修改完成後，若環境有可用 Python，先執行：

```powershell
python -m py_compile bot_all_in_one.py
```

4. 若本機 Python 不在 PATH，改使用已知安裝路徑，或明確告知無法做語法檢查。
5. 修改完成後重啟服務，讓程式載入新版本；若有新增、刪除或改名 slash command，重啟後確認 log 出現指令同步成功。
6. 不再依賴 `synced.flag` 控制同步，不要要求使用者手動刪除該檔案。
7. 重啟 `PunchBotService` 需要系統管理員權限。Codex 若要執行重啟命令，必須向使用者請求授權。
8. 重啟後查看 `bot.log` 最新內容，確認 bot 啟動成功、沒有語法錯誤、`auto_punch_task` 已啟動。

## 完成修改後的 GitHub 同步流程

每次完成修改與驗證後，請依序執行：

```powershell
git status
git add .
git commit -m "update project"
git push origin main
```

若 `git push origin main` 失敗，先停止後續操作，不要自行改分支、rebase、reset 或 force push；請回報錯誤原因並等待使用者確認。

一般重啟優先使用現有 `start.bat`，但它會要求互動選擇 `Y/N`。如果要完全自動化，需另外建立非互動重啟腳本，並分成「一般重啟」與「重新同步指令重啟」兩種，避免每次都誤 sync。

## 常見檢查點

- 今天是否為週末。
- 今天是否為休假日。
- 今天是否取消自動打卡。
- 今天是否為值班日。
- 昨天是否為值班日，今天是否需要打值班下班卡。
- 使用者是否已綁定 `empid` 與 `password`。
- `auto_punch` 是否開啟。
- 今日排程是否已存在。
- 今日對應 key 是否已在 `punched_today.json`。
- e-HR 是否已查到實際刷卡紀錄。

## 服務操作

此 bot 可能透過 NSSM 以 Windows service 形式執行。`start.bat` 會：

- 停止 `PunchBotService`
- 終止既有 `python.exe`
- 啟動 `PunchBotService`

修改程式後若要重啟，請注意這會影響正在等待的自動打卡流程。重啟前先確認是否接近打卡時間。

## 安全提醒

目前程式與 JSON 檔案內可能存在明文 token、員工編號與密碼。後續代理在任何回覆、commit message、文件或 log 摘要中，都不應重述這些敏感值。若需要改善專案，優先事項是：

- 將 Discord token 改由環境變數讀取。
- 將員工密碼加密或改為更安全的儲存方式。
- 將敏感 JSON 與 log 排除在版本控制之外。
- 增加啟動前檢查，避免 token 缺失或資料檔格式錯誤時靜默失敗。

## 開發態度

這是一個與員工出勤紀錄直接相關的內部工具。任何看似小的改動，都可能影響同仁是否準時打卡。請用保守、可驗證、可回復的方式開發；在不確定 e-HR 行為時，寧可通知使用者確認，也不要默默假設成功。
