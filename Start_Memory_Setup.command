#!/bin/bash
# 1-Click macOS Launcher for Memory Setup Control Center
set -euo pipefail

cd "$(dirname "$0")"
VENV_PY=".venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    echo ">>> Creating local Python 3.12 virtual environment..."
    if command -v uv >/dev/null 2>&1; then
        uv venv .venv --python 3.12
        uv pip install --python "$VENV_PY" -e ".[heif]"
    else
        python3 -m venv .venv
        "$VENV_PY" -m pip install -e ".[heif]"
    fi
fi

export LLAMA_ARG_CACHE_RAM=0

echo ">>> Starting Memory Setup Control Center..."
exec "$VENV_PY" -m memory_setup.cli "$@"
