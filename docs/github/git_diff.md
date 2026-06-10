# git diff 是什麼

`git diff` 是 Git 裡用來查看「檔案修改前後差異」的指令。

簡單說，它可以讓你知道：

- 哪些檔案被修改。
- 哪些內容被新增。
- 哪些內容被刪除。
- 修改內容是否不小心包含密碼、token 或不該上傳的資料。

## 常用指令

查看目前尚未提交的修改：

```powershell
git diff
```

只看某一個檔案的修改：

```powershell
git diff bot_all_in_one.py
```

查看已加入暫存區，但還沒提交的修改：

```powershell
git diff --staged
```

查看最近一次提交改了什麼：

```powershell
git show
```

## 為什麼這個專案需要 git diff

這個資料夾裡有打卡機器人程式，也有敏感資料，例如員工資料、密碼、Discord token、log。每次修改程式或準備上傳 GitHub 前，使用 `git diff` 可以先確認：

- 沒有把密碼寫進程式或文件。
- 沒有誤改自動打卡時間。
- 沒有誤刪防止重複打卡的邏輯。
- 沒有把使用者資料或 log 當成程式碼提交。

## 看 diff 的基本方式

`+` 代表新增內容：

```diff
+ 新增這一行
```

`-` 代表刪除內容：

```diff
- 刪除這一行
```

沒有符號的行通常是上下文，幫你看修改發生在哪裡。

## 注意

如果電腦沒有安裝 Git，執行 `git diff` 會失敗。需要先安裝 Git for Windows：

https://git-scm.com/download/win
