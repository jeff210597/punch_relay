@echo off
openfiles >nul 2>&1
if %errorlevel% neq 0 (
    echo Please right-click and run as Administrator
    pause
    exit
)
title Bot Launcher
set "ROOT_DIR=%~dp0"
set "NSSM_PATH=%ROOT_DIR%tools\nssm\win32\nssm.exe"

if exist "%ProgramFiles(x86)%" (
    set "NSSM_PATH=%ROOT_DIR%tools\nssm\win64\nssm.exe"
)

if not exist "%NSSM_PATH%" (
    echo [ERROR] NSSM executable not found:
    echo %NSSM_PATH%
    pause
    exit /b 1
)

echo ================================
echo         Bot Launcher
echo ================================
echo.
echo Stopping old Bot...
"%NSSM_PATH%" stop PunchBotService 2>nul
taskkill /f /im python.exe 2>nul
timeout /t 2 /nobreak > nul
echo.
if exist "%ROOT_DIR%synced.flag" (
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
        del "%ROOT_DIR%synced.flag"
        echo Sync flag removed, will resync...
    )
) else (
    echo Status: Not synced yet, will sync now
)
echo.
echo Starting Bot...
"%NSSM_PATH%" start PunchBotService
echo.
echo [OK] Bot started! Closing in 3 seconds...
timeout /t 3 /nobreak > nul
