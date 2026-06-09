# GitHub 安全上傳教學

這個專案可以上傳 GitHub，但不要把整個資料夾原封不動上傳，因為裡面有敏感資料。

## 一、不要上傳的檔案

已經在 `.gitignore` 排除：

- `punch_data.json`
- `punched_today.json`
- `schedule_today.json`
- `bot.log`
- `synced.flag`
- `.env`
- `nssm.exe`

## 二、先安裝 Git

下載 Git for Windows：

https://git-scm.com/download/win

安裝完成後，重新開啟 PowerShell，確認：

```powershell
git --version
```

## 三、初始化 Git

在 PowerShell 執行：

```powershell
cd C:\punch_relay
git init
git status
```

## 四、確認要上傳的檔案

執行：

```powershell
git status
```

正常情況下，應該看到可提交的程式與文件，例如：

- `.gitignore`
- `.env.example`
- `README.md`
- `agent.md`
- `bot_all_in_one.py`
- `git_diff.md`
- `requirements.txt`
- `start.bat`
- `vscode_tunnel_mobile_guide.md`
- `github_upload_guide.md`

不應該看到：

- `punch_data.json`
- `bot.log`
- `.env`
- `nssm.exe`

## 五、檢查修改內容

```powershell
git diff
```

如果看到 token、密碼、員工資料，先停止，不要提交。

## 六、提交

```powershell
git add .
git status
git commit -m "Initial punch bot project"
```

## 七、建立 GitHub repo

1. 到 https://github.com/new
2. 建立一個新的 repository。
3. 建議先設為 Private。
4. 不要勾選自動建立 README，因為本地已經有 README。

## 八、推送到 GitHub

把下面網址換成你的 repo：

```powershell
git branch -M main
git remote add origin https://github.com/你的帳號/你的repo.git
git push -u origin main
```

## 九、之後修改程式的流程

每次修改後：

```powershell
git status
git diff
git add .
git commit -m "Describe your change"
git push
```

## 十、重要提醒

如果以前已經把 token 或密碼提交到 GitHub，即使後來刪掉，也要視為已外洩，應立即更換 Discord token 與相關密碼。
