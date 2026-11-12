#!/bin/bash
cd "$(dirname "$0")"
if [ -d "viewer" ]; then
    cd viewer
fi
python3 server.py
