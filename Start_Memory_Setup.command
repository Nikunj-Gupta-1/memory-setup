#!/bin/bash
# 1-Click macOS Setup & Launcher for Memory Setup Control Center Studio
set -euo pipefail

cd "$(dirname "$0")"
VENV_PY=".venv/bin/python"
APP_BUNDLE="Memory Setup.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
APP_MACOS="$APP_CONTENTS/MacOS"
APP_RESOURCES="$APP_CONTENTS/Resources"
APP_BIN="$APP_MACOS/MemorySetup"
APP_PLIST="$APP_CONTENTS/Info.plist"
APP_ICNS="$APP_RESOURCES/MemorySetup.icns"
ICON_PNG="memory_setup/gui/static/icon.png"

ensure_venv() {
    local force_refresh="${1:-0}"
    if [ ! -x "$VENV_PY" ]; then
        echo ">>> Creating local Python 3.12 virtual environment..."
        if command -v uv >/dev/null 2>&1; then
            uv venv .venv --python 3.12 --seed
            uv pip install --python "$VENV_PY" -e ".[heif,gui]"
        else
            python3 -m venv .venv
            "$VENV_PY" -m pip install -e ".[heif,gui]"
        fi
    elif [ "$force_refresh" = "1" ]; then
        echo ">>> Refreshing editable package install in .venv..."
        if command -v uv >/dev/null 2>&1; then
            uv pip install --python "$VENV_PY" -e ".[heif,gui]"
        else
            "$VENV_PY" -m pip install -e ".[heif,gui]"
        fi
    fi
}

build_app_bundle() {
    echo ">>> Building macOS app bundle: $APP_BUNDLE..."
    mkdir -p "$APP_MACOS" "$APP_RESOURCES"

    cat > "$APP_PLIST" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleDisplayName</key>
    <string>Memory Setup</string>
    <key>CFBundleExecutable</key>
    <string>MemorySetup</string>
    <key>CFBundleIconFile</key>
    <string>MemorySetup</string>
    <key>CFBundleIdentifier</key>
    <string>com.nikunjgupta.memory-setup</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>Memory Setup</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsArbitraryLoads</key>
        <true/>
    </dict>
</dict>
</plist>
EOF

    if [ -f "$ICON_PNG" ] && command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
        local iconset_tmp
        iconset_tmp="$(mktemp -d)"
        local iconset_dir="$iconset_tmp/MemorySetup.iconset"
        mkdir -p "$iconset_dir"
        sips -z 16 16     "$ICON_PNG" --out "$iconset_dir/icon_16x16.png" >/dev/null 2>&1 || true
        sips -z 32 32     "$ICON_PNG" --out "$iconset_dir/icon_16x16@2x.png" >/dev/null 2>&1 || true
        sips -z 32 32     "$ICON_PNG" --out "$iconset_dir/icon_32x32.png" >/dev/null 2>&1 || true
        sips -z 64 64     "$ICON_PNG" --out "$iconset_dir/icon_32x32@2x.png" >/dev/null 2>&1 || true
        sips -z 128 128   "$ICON_PNG" --out "$iconset_dir/icon_128x128.png" >/dev/null 2>&1 || true
        sips -z 256 256   "$ICON_PNG" --out "$iconset_dir/icon_128x128@2x.png" >/dev/null 2>&1 || true
        sips -z 256 256   "$ICON_PNG" --out "$iconset_dir/icon_256x256.png" >/dev/null 2>&1 || true
        sips -z 512 512   "$ICON_PNG" --out "$iconset_dir/icon_256x256@2x.png" >/dev/null 2>&1 || true
        sips -z 512 512   "$ICON_PNG" --out "$iconset_dir/icon_512x512.png" >/dev/null 2>&1 || true
        cp "$ICON_PNG" "$iconset_dir/icon_512x512@2x.png" >/dev/null 2>&1 || true
        iconutil -c icns "$iconset_dir" -o "$APP_ICNS" >/dev/null 2>&1 || cp "$ICON_PNG" "$APP_ICNS"
        rm -rf "$iconset_tmp"
    elif [ -f "$ICON_PNG" ]; then
        cp "$ICON_PNG" "$APP_ICNS"
    fi

    if [ -f "scripts/macos_launcher.c" ] && command -v clang >/dev/null 2>&1; then
        clang -O2 -Wall -Wextra -arch arm64 -arch x86_64 -o "$APP_BIN" scripts/macos_launcher.c
        chmod +x "$APP_BIN"
    fi

    touch "$APP_BUNDLE"
    echo ">>> Built $APP_BUNDLE cleanly."
}

if [ "${1:-}" = "--rebuild-app" ]; then
    rm -rf "$APP_BUNDLE"
    ensure_venv 1
    build_app_bundle
    exit 0
fi

ensure_venv 0

if [ ! -f "$APP_PLIST" ] || [ ! -f "$APP_ICNS" ] || [ ! -x "$APP_BIN" ]; then
    build_app_bundle
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
