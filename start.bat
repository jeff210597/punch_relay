@echo off
openfiles >nul 2>&1
if %errorlevel% neq 0 (
    echo Please right-click and run as Administrator
    pause
    exit
)
title Bot Launcher
echo ================================
echo         Bot Launcher
echo ================================
echo.
echo Stopping old Bot...
"C:\punch_relay\nssm.exe" stop PunchBotService 2>nul
taskkill /f /im python.exe 2>nul
timeout /t 2 /nobreak > nul
echo.
if exist "C:\punch_relay\synced.flag" (
    echo Status: Commands already synced
    echo.
    echo Resync Discord commands? [Y=Yes / N=No]
    echo - Press N for normal restart
    echo - Press Y only after adding or removing commands
    echo.
    choice /c YN /n /m "Choose [Y/N]: "
    if errorlevel 2 (
        echo Skipping resync, starting Bot...
    ) else (
        del "C:\punch_relay\synced.flag"
        echo Sync flag removed, will resync...
    )
) else (
    echo Status: Not synced yet, will sync now
)
echo.
echo Starting Bot...
"C:\punch_relay\nssm.exe" start PunchBotService
echo.
echo [OK] Bot started! Closing in 3 seconds...
timeout /t 3 /nobreak > nul
