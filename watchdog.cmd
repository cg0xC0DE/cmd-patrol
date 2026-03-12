@echo off
rem cmd-patrol watchdog
rem Called by Windows Task Scheduler every 1 minute.
rem If the backend is not responding, re-launch start.cmd.

netstat -ano | findstr "LISTENING" | findstr ":51314 " >nul 2>&1
if %errorlevel% equ 0 (
    echo [watchdog] %date% %time% Backend is running, all good.
    exit /b 0
)

echo [watchdog] %date% %time% Backend not responding, relaunching...
cscript //nologo "%~dp0start_hidden.vbs"
