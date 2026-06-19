# 一次性任務：支援 32 / 64 位元 NSSM

請先閱讀根目錄的 `agent.md`，遵守其中的安全、驗證、GitHub 上傳與重啟規則。

## 任務目標

本 repository 已新增兩個 NSSM 執行檔：

```text
tools/nssm/win32/nssm.exe
tools/nssm/win64/nssm.exe
```

請修改 `start.bat`，讓它依目前 Windows 系統架構自動使用正確版本的 NSSM。

## 修改要求

1. 不再使用固定路徑：

```bat
"C:\punch_relay\nssm.exe"
```

2. 在 `start.bat` 的 `title Bot Launcher` 前或後，設定 NSSM 路徑：

```bat
set "NSSM_PATH=C:\punch_relay\tools\nssm\win32\nssm.exe"

if exist "%ProgramFiles(x86)%" (
    set "NSSM_PATH=C:\punch_relay\tools\nssm\win64\nssm.exe"
)
```

3. 將原本所有 NSSM 呼叫改為：

```bat
"%NSSM_PATH%" stop PunchBotService 2>nul
```

以及：

```bat
"%NSSM_PATH%" start PunchBotService
```

4. 在執行前增加檢查。若 `%NSSM_PATH%` 不存在，要顯示明確錯誤訊息、暫停並結束，不可繼續執行。

建議格式：

```bat
if not exist "%NSSM_PATH%" (
    echo [ERROR] NSSM executable not found:
    echo %NSSM_PATH%
    pause
    exit /b 1
)
```

5. 不要修改 `bot_all_in_one.py`、打卡規則、Discord 設定、環境變數或服務名稱 `PunchBotService`。

## 驗證要求

完成後請：

1. 檢查 `start.bat` 中不應再出現 `C:\punch_relay\nssm.exe`。
2. 確認 `tools/nssm/win32/nssm.exe` 與 `tools/nssm/win64/nssm.exe` 存在。
3. 執行 `git diff`，確認只包含必要修改。
4. 不要重啟服務；本次只修改啟動腳本。
5. commit 並 push 到 `main`。

Commit message：

```text
Support bundled NSSM binaries by Windows architecture
```

## 收尾

確認 push 成功後，請刪除本檔案：

```text
docs/codex/TODO_nssm_architecture.md
```

並建立第二個 commit：

```text
Remove completed NSSM migration task
```

最後回報：

* 修改了哪些檔案
* 使用哪個 NSSM 路徑判斷方式
* 兩個 commit 是否都成功 push
* 是否有任何錯誤或未完成事項
