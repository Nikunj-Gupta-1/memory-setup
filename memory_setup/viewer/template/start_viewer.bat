@echo off
setlocal
cd /d "%~dp0"
if exist "viewer\server.py" (
    cd viewer
)

where python >nul 2>nul
if %errorlevel%==0 (
    python server.py
) else (
    py -3 server.py
)
pause
