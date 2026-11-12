@echo off
REM 1-Click Windows Launcher for Memory Setup Control Center
setlocal
cd /d "%~dp0"

set "PY_CMD="
where python >nul 2>nul
if %errorlevel%==0 (
    set "PY_CMD=python"
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        set "PY_CMD=py -3"
    )
)

if "%PY_CMD%"=="" (
    echo [ERROR] Python 3.10+ was not found on PATH.
    echo Please install Python from https://www.python.org/downloads/windows/
    echo IMPORTANT: Check "Add python.exe to PATH" during installation.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating local Python virtual environment...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo Installing Memory Setup and dependencies...
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -e ".[heif]"
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)

set LLAMA_ARG_CACHE_RAM=0
echo Starting Memory Setup Control Center...
.venv\Scripts\python.exe -m memory_setup.cli %*
pause
