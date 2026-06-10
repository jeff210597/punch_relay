# VS Code Tunnel 手機遠端修改教學

這份教學適合用手機瀏覽器遠端修改 `C:\punch_relay\bot_all_in_one.py`。

## 一、在機器人電腦安裝 VS Code

1. 到 VS Code 官方網站下載：
   https://code.visualstudio.com/
2. 安裝完成後開啟 VS Code。
3. 開啟資料夾：
   `C:\punch_relay`

## 二、登入帳號

VS Code Tunnel 需要使用 GitHub 或 Microsoft 帳號登入。

1. 在 VS Code 左下角點帳號圖示。
2. 選擇 `Sign in with GitHub` 或 `Sign in with Microsoft`。
3. 依照畫面完成登入。

## 三、開啟 Remote Tunnel

1. 在 VS Code 按 `Ctrl + Shift + P`。
2. 輸入：

```text
Remote Tunnels: Turn on Remote Tunnel Access
```

3. 選取該指令。
4. 如果跳出授權畫面，依指示登入。
5. 記下 VS Code 顯示的 tunnel 名稱。

## 四、用手機瀏覽器連線

1. 手機打開瀏覽器。
2. 前往：
   https://vscode.dev
3. 使用同一個 GitHub 或 Microsoft 帳號登入。
4. 左下角或遠端選單選擇 `Remote Tunnel`。
5. 選擇你的電腦 tunnel。
6. 開啟資料夾：
   `C:\punch_relay`

## 五、手機修改機器人程式

1. 在檔案列表打開：
   `bot_all_in_one.py`
2. 修改程式。
3. 儲存檔案。
4. 若有修改 Discord slash command，記得之後重啟時要重新同步指令。

## 六、重啟機器人

如果你已經遠端進到 VS Code terminal，可以執行：

```powershell
C:\punch_relay\start.bat
```

如果無法從手機 terminal 操作，也可以搭配 Chrome 遠端桌面或 AnyDesk 重啟。

## 七、注意事項

- 不要在手機瀏覽器公開環境登入 VS Code Tunnel。
- 不要把 Discord token、員工密碼或 `.env` 上傳 GitHub。
- 修改前先備份或使用 Git commit 保存版本。
- 接近上下班打卡時間時，不建議重啟服務，避免影響排程。

## 八、常見問題

如果手機連不上 tunnel：

- 確認機器人電腦有開機。
- 確認 VS Code 還開著。
- 確認 Remote Tunnel Access 已啟用。
- 確認手機登入的是同一個帳號。

如果改完程式機器人沒有更新：

- 確認檔案已儲存。
- 重啟 `PunchBotService`。
- 若改了 slash command，刪除 `synced.flag` 後重新啟動，讓 Discord 指令重新同步。
