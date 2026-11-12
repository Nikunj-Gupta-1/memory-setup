from __future__ import annotations

import http.server
import json
import mimetypes
import platform
import socketserver
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

from memory_setup.config import ArchiveContext, OLLAMA_DEFAULT_MODEL
from memory_setup.pipeline_runner import PipelineManager, get_archive_metrics
from memory_setup.setup.checker import run_diagnostics
from memory_setup.setup.installer import ensure_ollama_running, pull_ollama_model
from memory_setup.viewer.builder import (
    merge_person_clusters,
    quarantine_homework_files,
    rename_person_clusters,
    run_stage5_museum,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def browse_native_folder() -> Optional[str]:
    """Open native OS folder picker (macOS Finder via osascript, fallback to tkinter)."""
    if platform.system() == "Darwin":
        try:
            proc = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'POSIX path of (choose folder with prompt "Select Archive or Raw Media Folder")',
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip().rstrip("/")
            return None
        except Exception:
            pass

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        selected = filedialog.askdirectory(title="Select Archive or Raw Media Folder")
        root.destroy()
        return selected or None
    except Exception:
        return None


class AppState:
    def __init__(self, default_archive: Optional[Path] = None) -> None:
        self.manager = PipelineManager()
        self.selected_archive: Optional[Path] = (
            Path(default_archive).expanduser().resolve() if default_archive else None
        )
        if self.selected_archive:
            self.manager.active_ctx = ArchiveContext(self.selected_archive)
        self.pull_status: Dict[str, Any] = {"running": False, "status": "idle"}


def create_app_server(
    default_archive: Optional[Path] = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> socketserver.ThreadingTCPServer:
    state = AppState(default_archive=default_archive)

    class ControlCenterHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _send_json(self, code: int, payload: Any) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _serve_file(self, file_path: Path) -> None:
            if not file_path.exists() or not file_path.is_file():
                self.send_error(404, "File not found")
                return
            ctype, _ = mimetypes.guess_type(str(file_path))
            if not ctype:
                ctype = "application/octet-stream"
            size = file_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(file_path, "rb") as fp:
                while True:
                    chunk = fp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        def _read_json_body(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            return json.loads(raw.decode("utf-8")) if raw else {}

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == "/" or path == "/index.html":
                self._serve_file(STATIC_DIR / "index.html")
                return
            if path in ("/app.js", "/style.css"):
                self._serve_file(STATIC_DIR / path.lstrip("/"))
                return

            if path == "/api/status":
                status = state.manager.get_status()
                status["pull"] = state.pull_status
                status["selected_archive"] = str(state.selected_archive) if state.selected_archive else ""
                if state.selected_archive and "archive" not in status:
                    status["archive"] = get_archive_metrics(state.selected_archive)
                self._send_json(200, status)
                return

            if path == "/api/pipeline/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    for _ in range(1200):
                        payload = json.dumps(state.manager.get_status())
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        time.sleep(0.8)
                except (BrokenPipeError, ConnectionResetError):
                    return
                return

            # Serve files from the selected archive under /archive/
            if path.startswith("/archive/") and state.selected_archive is not None:
                rel = urllib.parse.unquote(path[len("/archive/") :]).lstrip("/")
                target = (state.selected_archive / rel).resolve()
                root_resolved = state.selected_archive.resolve()
                # Allow symlinked content/ inside viewer/content/ as long as target stays inside archive_root
                if str(target).startswith(str(root_resolved)):
                    self._serve_file(target)
                    return
                self.send_error(403, "Forbidden")
                return

            self.send_error(404, "Not found")

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                body = self._read_json_body()
            except Exception:
                self._send_json(400, {"error": "Invalid JSON body"})
                return

            if path == "/api/browse-folder":
                chosen = browse_native_folder()
                if chosen:
                    state.selected_archive = Path(chosen).resolve()
                    state.manager.active_ctx = ArchiveContext(state.selected_archive)
                self._send_json(200, {"path": chosen})
                return

            if path == "/api/preflight":
                raw_path = body.get("archive_path") or (str(state.selected_archive) if state.selected_archive else None)
                mode = body.get("mode", "in_place")
                arch_path = Path(raw_path).expanduser().resolve() if raw_path else None
                if arch_path:
                    state.selected_archive = arch_path
                    state.manager.active_ctx = ArchiveContext(arch_path, mode=mode)
                diag = run_diagnostics(archive_path=arch_path, mode=mode)
                if arch_path:
                    diag["archive"] = get_archive_metrics(arch_path)
                self._send_json(200, diag)
                return

            if path == "/api/setup/start-ollama":
                res = ensure_ollama_running()
                self._send_json(200, res)
                return

            if path == "/api/setup/pull-model":
                model = body.get("model", OLLAMA_DEFAULT_MODEL)
                if not state.pull_status.get("running"):
                    state.pull_status = {"running": True, "status": f"Pulling {model}..."}

                    def _pull_worker() -> None:
                        def _cb(evt: Dict[str, Any]) -> None:
                            state.pull_status = {
                                "running": True,
                                "status": evt.get("status", "downloading"),
                                "completed": evt.get("completed", 0),
                                "total": evt.get("total", 0),
                            }

                        res = pull_ollama_model(model=model, progress_cb=_cb)
                        state.pull_status = {"running": False, **res}

                    threading.Thread(target=_pull_worker, daemon=True).start()
                self._send_json(200, {"ok": True, "pull": state.pull_status})
                return

            if path == "/api/pipeline/start":
                raw_path = body.get("archive_path") or (str(state.selected_archive) if state.selected_archive else None)
                if not raw_path:
                    self._send_json(400, {"error": "No archive folder selected."})
                    return
                arch_path = Path(raw_path).expanduser().resolve()
                state.selected_archive = arch_path
                ctx = ArchiveContext(
                    archive_root=arch_path,
                    mode=body.get("mode", "in_place"),
                    speed_mode=body.get("speed_mode", "whisper"),
                    enable_biometrics=bool(body.get("enable_biometrics", True)),
                    enable_vision=bool(body.get("enable_vision", True)),
                )
                stages = body.get("stages")
                started = state.manager.start(ctx, stages=stages)
                self._send_json(200, {"started": started, "status": state.manager.get_status()})
                return

            if path == "/api/pipeline/stop":
                state.manager.stop()
                self._send_json(200, {"ok": True, "status": state.manager.get_status()})
                return

            if path == "/api/pipeline/sync":
                raw_path = body.get("archive_path") or (str(state.selected_archive) if state.selected_archive else None)
                if not raw_path:
                    self._send_json(400, {"error": "No archive selected."})
                    return
                ctx = ArchiveContext(Path(raw_path).expanduser().resolve())
                res = run_stage5_museum(ctx)
                self._send_json(200, {"ok": True, "result": res, "archive": get_archive_metrics(ctx.archive_root)})
                return

            if path == "/api/people/rename" and state.selected_archive is not None:
                ctx = ArchiveContext(state.selected_archive)
                count = rename_person_clusters(ctx, body.get("updates", []))
                self._send_json(200, {"ok": True, "updated": count})
                return

            if path == "/api/people/merge" and state.selected_archive is not None:
                ctx = ArchiveContext(state.selected_archive)
                merge_person_clusters(ctx, int(body["source_id"]), int(body["target_id"]))
                self._send_json(200, {"ok": True})
                return

            if path == "/api/homework/quarantine" and state.selected_archive is not None:
                ctx = ArchiveContext(state.selected_archive)
                moved = quarantine_homework_files(ctx)
                self._send_json(200, {"ok": True, "moved_count": moved})
                return

            self._send_json(404, {"error": "Unknown API endpoint"})

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    for candidate_port in range(port, port + 20):
        try:
            return socketserver.ThreadingTCPServer((host, candidate_port), ControlCenterHandler)
        except OSError:
            continue
    return socketserver.ThreadingTCPServer((host, 0), ControlCenterHandler)


def launch_gui(
    default_archive: Optional[Path] = None,
    port: int = 8765,
    use_webview: bool = True,
    open_browser: bool = True,
) -> None:
    """Start the Control Center server and open it in pywebview or the default browser."""
    server = create_app_server(default_archive=default_archive, port=port)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"

    if use_webview:
        try:
            import webview  # type: ignore

            srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
            srv_thread.start()
            webview.create_window("Memory Setup — Local Media Intelligence", url, width=1240, height=840)
            webview.start()
            server.shutdown()
            return
        except Exception:
            pass

    print("=" * 64)
    print("MEMORY SETUP — LOCAL MEDIA INTELLIGENCE CONTROL CENTER")
    print(f"Control Center URL: {url}")
    if default_archive:
        print(f"Default Archive:    {Path(default_archive).expanduser().resolve()}")
    print("Press Ctrl+C to stop.")
    print("=" * 64)

    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMemory Setup stopped cleanly.")
        server.shutdown()
