---
name: punch-procedure
description: 維護與部署 punch_relay Discord bot 時使用的 repository-local 操作規則。
---

# Punch Relay Skill

在處理 `punch_relay` bot、Windows 服務腳本、部署檔案、GitHub 同步或執行狀態驗證時，使用這份規則。

## 核心規則

在任何 punch relay 專案資料夾內完成變更後，應將預期要進 repository 的變更同步到：

```text
https://github.com/jeff210597/punch_relay
```

不要讓可部署程式碼、服務腳本、文件或內建工具變更只留在主機本機。runtime 狀態與密鑰必須留在本機。

除非使用者明確停用自動上傳，維護中的主機應保持 Windows 自動同步服務已安裝且正在執行：

```text
PunchRelayGitSync
```

## 永遠不要 Commit

不要 stage 或上傳下列檔案：

- `.env`
- `punch_data.json`
- `punched_today.json`
- `schedule_today.json`
- `admin_alerts_today.json`
- `bot.log`
- `github_sync.log`
- `github_sync_watcher.log`
- `github_sync_service.log`
- `synced.flag`
- `*.bak`
- `__pycache__/`
- `.codex-remote-attachments/`

在任何 commit 或 GitHub API 更新前，先掃描真實密鑰：

```powershell
rg -n "DISCORD_TOKEN|EHR_BASE|pwd|password|token|密碼" README.md docs bot_all_in_one.py restart_bot_admin.ps1 restart_bot_resync_admin.ps1 .env.example agent.md skill.md sync_to_github.ps1 watch_github_sync.ps1 install_github_sync_watcher_admin.ps1
```

變數名稱與 placeholder 可以存在。真實 Discord token、GitHub token、e-HR URL、密碼、cookie 和使用者資料不能存在。

## 必要驗證

若變更程式碼，執行：

```powershell
python -m py_compile bot_all_in_one.py
```

若變更部署或腳本，也要確認：

```powershell
Test-Path tools\nssm\win32\nssm.exe
Test-Path tools\nssm\win64\nssm.exe
rg -n "C:\\punch_relay|C:\\Users\\7b\\Documents\\punch_relay" README.md docs agent.md skill.md bot_all_in_one.py start.bat restart_bot_admin.ps1 restart_bot_resync_admin.ps1 install_nssm_service_admin.ps1 sync_to_github.ps1 watch_github_sync.ps1 install_github_sync_watcher_admin.ps1
```

路徑掃描不應出現固定專案根目錄依賴。腳本應使用自身所在位置作為專案根目錄。

## GitHub 同步流程

1. 一般主機變更優先使用內建同步腳本：

```powershell
powershell -ExecutionPolicy Bypass -File .\sync_to_github.ps1
```

2. 同步腳本失敗，或較大範圍修改前，手動檢查本機變更：

```powershell
git status --short --branch
git diff
```

3. 只 stage 預期進 repository 的檔案。優先使用明確檔案路徑；有 runtime 檔案時不要做大範圍 staging。

4. 使用清楚的 commit message。

5. Push 到 `main`：

```powershell
git push origin main
```

## 自動同步服務

用系統管理員 PowerShell 安裝 watcher：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_github_sync_watcher_admin.ps1
```

watcher 會執行 `watch_github_sync.ps1`，忽略本機 runtime 與密鑰檔案，在變更穩定 45 秒後執行 `sync_to_github.ps1`。同步腳本會拒絕 stage `.env`、runtime JSON、log、備份檔、明顯的 Discord/GitHub token，或類似密碼的 staged 內容。watcher log 應和 NSSM stdout/stderr log 分開，避免服務鎖住自己的 log 檔。

## 穩定 GitHub 驗證政策

GitHub 驗證缺失時，不要一直重試 `git push`，這會浪費時間與 token。

曾發生的卡住原因：`sync_to_github.ps1` 已經完成本機 commit，但 `git push` 進入 GitHub HTTPS credential helper/互動驗證流程，導致沒有 `push complete`，本機狀態變成 `main...origin/main [ahead 1]`，GitHub 看不到更新。

使用這個順序：

1. 先嘗試一次正常 push：

```powershell
git push origin main
```

2. 如果卡住或 timeout，先檢查狀態與 log：

```powershell
git status --short --branch
git log --oneline --decorate -5
Get-Content .\github_sync.log -Tail 80
```

如果狀態顯示 `[ahead 1]` 且 log 停在 `staged files:` 或 commit 後沒有 `push complete`，代表本機已 commit、遠端未收到。

3. 執行一次非互動診斷，避免再次觸發 credential helper 視窗或卡住：

```powershell
$env:GIT_TERMINAL_PROMPT='0'
git -c credential.helper= push origin main
```

4. 如果診斷訊息表示無法讀取 GitHub username，就停止重試本機 push。穩定方案是讓服務或目前 shell 有 `GITHUB_PAT`，再執行同步；沒有 PAT 時，`sync_to_github.ps1` 應使用非互動 push 快速失敗，不應等待 GitHub credential helper。

5. 若要用本機 Git 長期 push，繼續前建立穩定 credential：

```powershell
git config --global credential.helper manager
git push origin main
```

出現提示時完成一次 GitHub browser/device login。之後的 push 應會重用已儲存 credential。

6. 如果使用者提供 GitHub PAT，只能用於 GitHub credential setup、單次 env-only push，或本機 `PunchRelayGitSync` 服務環境。不要把 PAT 寫進 repository 檔案、log、remote、script 或文件。

7. 如果主機無法完成 GitHub credential setup，且 GitHub connector tools 可用，少量文字更新可改用 connector/API 路徑。API 更新成功後，執行 `git fetch origin`，確認 GitHub 已更新。若本機之前已有未推上的重複 commit，且使用者同意直接對齊，才能執行 `git reset --hard origin/main`。

8. 除非使用者明確要求，不要 force-push、reset 或 rebase。

## 部署預期

repository 應足以重建新主機：

1. Clone repository。
2. 安裝 Python，並執行 `pip install -r requirements.txt`。
3. 複製 `.env.example` 成 `.env`，本機填入密鑰。
4. 執行 `python -m py_compile bot_all_in_one.py`。
5. 以系統管理員身分執行 `install_nssm_service_admin.ps1`。
6. slash commands 有變更時，執行 `restart_bot_resync_admin.ps1`。
7. 以系統管理員身分執行 `install_github_sync_watcher_admin.ps1`，啟用 GitHub 自動同步。

repository 包含兩個 NSSM 執行檔：

- `tools/nssm/win32/nssm.exe`
- `tools/nssm/win64/nssm.exe`

不要要求固定安裝目錄，例如 `C:\punch_relay`。
