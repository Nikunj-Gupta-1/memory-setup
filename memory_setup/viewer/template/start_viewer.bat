@echo off
cd /d "%~dp0"
if exist "viewer\server.py" (
    cd viewer
)
python server.py
pause
