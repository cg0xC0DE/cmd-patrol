@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0\backend"

rem --- Locate uv: PATH first, then user local ---
set "UV="
where uv >nul 2>&1
if %errorlevel% equ 0 (
    set "UV=uv"
) else if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "UV=%USERPROFILE%\.local\bin\uv.exe"
)
if not defined UV (
    echo [cmd-patrol] uv not found. Please run init.cmd first.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo [cmd-patrol] .venv not found. Please run init.cmd first.
    pause
    exit /b 1
)

rem --- Set global env var so other projects can discover the MQ endpoint ---
set "CMD_PATROL_URL=http://127.0.0.1:51314"
setx CMD_PATROL_URL "%CMD_PATROL_URL%" >nul 2>&1

echo [cmd-patrol] Starting server on %CMD_PATROL_URL%
start "" "%CMD_PATROL_URL%"
"%UV%" run python app.py
