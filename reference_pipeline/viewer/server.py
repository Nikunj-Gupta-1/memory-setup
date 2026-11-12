#!/usr/bin/env python3
import http.server
import socketserver
import webbrowser
import os
import sys
from pathlib import Path

# Find the archive root containing content/ and viewer/
CUR_DIR = Path(__file__).resolve().parent
if CUR_DIR.name == 'viewer':
    ROOT_DIR = CUR_DIR.parent
else:
    ROOT_DIR = CUR_DIR

os.chdir(ROOT_DIR)

PORT = 8080
# Try port 8080 or fallback to 8081..8090
for p in range(8080, 8095):
    try:
        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, format, *args):
                pass # Quiet logging for fast 60fps streaming

        httpd = socketserver.TCPServer(("", p), QuietHandler)
        PORT = p
        break
    except OSError:
        continue

url = f"http://localhost:{PORT}/viewer/viewer.html"
print("=" * 60)
print("🏛️  OFFLINE MUSEUM VIEWER LOCAL SERVER")
print("=" * 60)
print(f"Archive Root: {ROOT_DIR}")
print(f"Opening Browser at: {url}")
print("Press Ctrl+C to stop the viewer server.")
print("=" * 60)

webbrowser.open(url)

try:
    httpd.serve_forever()
except KeyboardInterrupt:
    print("\n✓ Viewer server stopped safely.")
