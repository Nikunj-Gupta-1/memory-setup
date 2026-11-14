#!/bin/bash
# 1-Click macOS Launcher for Memory Setup Control Center
set -euo pipefail

cd "$(dirname "$0")"
VENV_PY=".venv/bin/python"
APP_BUNDLE="Memory Setup.app"
APP_BIN="$APP_BUNDLE/Contents/MacOS/MemorySetup"

if [ ! -x "$VENV_PY" ]; then
    echo ">>> Creating local Python 3.12 virtual environment..."
    if command -v uv >/dev/null 2>&1; then
        uv venv .venv --python 3.12 --seed
        uv pip install --python "$VENV_PY" -e ".[heif,gui]"
    else
        python3 -m venv .venv
        "$VENV_PY" -m pip install -e ".[heif,gui]"
    fi
fi

# Build universal macOS .app launcher on demand if missing and clang is available
if [ ! -x "$APP_BIN" ] && [ -f "scripts/macos_launcher.c" ] && command -v clang >/dev/null 2>&1; then
    mkdir -p "$APP_BUNDLE/Contents/MacOS"
    clang -O2 -Wall -Wextra -arch arm64 -arch x86_64 -o "$APP_BIN" scripts/macos_launcher.c || true
fi

export LLAMA_ARG_CACHE_RAM=0

# If CLI arguments are supplied, run synchronously in the current shell
if [ "$#" -gt 0 ]; then
    exec "$VENV_PY" -m memory_setup.cli "$@"
fi

# Launch the GUI app detached so Terminal does not stay open
if [ -d "$APP_BUNDLE" ] && [ -x "$APP_BIN" ]; then
    open "$APP_BUNDLE"
else
    mkdir -p "$HOME/Library/Logs"
    nohup "$VENV_PY" -m memory_setup.cli >"$HOME/Library/Logs/MemorySetup.log" 2>&1 &
fi

# If launched by Finder double-click in Terminal.app, close this launcher window
if [ "${TERM_PROGRAM:-}" = "Apple_Terminal" ]; then
    (
        sleep 0.5
        osascript -e 'tell application "Terminal" to close (every window whose name contains "Start_Memory_Setup.command")' >/dev/null 2>&1 || true
    ) &
fi
