@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0\backend"

set UV=C:\Users\wangy\.local\bin\uv.exe

if not exist ".venv" (
    echo "[cmd-patrol] Creating venv with uv (Python 3.10)..."
    "%UV%" venv --python 3.10
    "%UV%" pip install -r requirements.txt
)

echo "[cmd-patrol] Starting server on http://127.0.0.1:5050"
start "" "http://127.0.0.1:5050"
"%UV%" run python app.py
