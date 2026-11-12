@echo off
REM 1-Click Windows Launcher for Memory Setup Control Center
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating local Python virtual environment...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -e ".[heif]"
)

set LLAMA_ARG_CACHE_RAM=0
echo Starting Memory Setup Control Center...
.venv\Scripts\python.exe -m memory_setup.cli %*
pause
