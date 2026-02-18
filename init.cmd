@echo off
setlocal EnableDelayedExpansion

echo ============================================================
echo   cmd-patrol - Environment Init
echo ============================================================
echo.

cd /d "%~dp0"

rem ============================================================
rem   Phase 1: Environment Check
rem ============================================================
echo [Phase 1] Environment Check
echo.

set "MISSING=0"

rem --- Check Python 3.10+ ---
set "PY="
where py >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=*" %%v in ('py -3 --version 2^>^&1') do set "PY_VER=%%v"
    echo   !PY_VER! detected via py launcher.
    for /f "tokens=2 delims= " %%v in ("!PY_VER!") do set "PY_FULL=%%v"
    for /f "tokens=1,2 delims=." %%a in ("!PY_FULL!") do (
        if %%a GEQ 3 if %%b GEQ 10 set "PY=py -3"
    )
)
if not defined PY (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "tokens=*" %%v in ('python --version 2^>^&1') do set "PY_VER=%%v"
        echo   !PY_VER! detected via python.
        for /f "tokens=2 delims= " %%v in ("!PY_VER!") do set "PY_FULL=%%v"
        for /f "tokens=1,2 delims=." %%a in ("!PY_FULL!") do (
            if %%a GEQ 3 if %%b GEQ 10 set "PY=python"
        )
    )
)
if defined PY (
    echo   [OK] Python 3.10+ found.
) else (
    echo   [FAIL] Python 3.10+ not found.
    echo          Install from https://www.python.org/downloads/ and add to PATH.
    set "MISSING=1"
)

rem --- Check uv ---
set "UV="
where uv >nul 2>&1
if %errorlevel% equ 0 (
    set "UV=uv"
    echo   [OK] uv found in PATH.
) else (
    if exist "%USERPROFILE%\.local\bin\uv.exe" (
        set "UV=%USERPROFILE%\.local\bin\uv.exe"
        echo   [OK] uv found at %USERPROFILE%\.local\bin\uv.exe
    ) else (
        echo   [WARN] uv not found. Attempting to install via pip...
        if defined PY (
            !PY! -m pip install uv -q
            where uv >nul 2>&1
            if !errorlevel! equ 0 (
                set "UV=uv"
                echo   [OK] uv installed successfully.
            ) else (
                echo   [FAIL] uv installation failed.
                echo          Install manually: pip install uv  OR  winget install astral-sh.uv
                set "MISSING=1"
            )
        ) else (
            echo   [FAIL] Cannot install uv without Python.
            set "MISSING=1"
        )
    )
)

echo.
if "!MISSING!"=="1" (
    echo   Some requirements are missing. Please install them and re-run init.cmd.
    pause
    exit /b 1
)
echo   [OK] All environment checks passed.
echo.

rem ============================================================
rem   Phase 2: Automated Installation
rem ============================================================
echo [Phase 2] Automated Installation
echo.

pushd backend

rem --- Create venv ---
if exist ".venv" (
    echo   [SKIP] .venv already exists.
) else (
    echo   [INFO] Creating venv with uv (Python 3.10)...
    "%UV%" venv --python 3.10
    if !errorlevel! neq 0 (
        echo   [FAIL] venv creation failed.
        popd
        pause
        exit /b 1
    )
    echo   [OK] venv created.
)

rem --- Install / sync dependencies ---
echo   [INFO] Installing dependencies from requirements.txt...
"%UV%" pip install -r requirements.txt -q
if !errorlevel! neq 0 (
    echo   [FAIL] Dependency installation failed.
    popd
    pause
    exit /b 1
)
echo   [OK] All dependencies installed.

popd

echo.
echo ============================================================
echo   Initialization complete!
echo   Run start.cmd to launch cmd-patrol.
echo ============================================================
echo.
pause
