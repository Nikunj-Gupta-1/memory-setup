from __future__ import annotations

import http.server
import json
import mimetypes
import os
import platform
import secrets
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from memory_setup.config import ArchiveContext, MIN_FREE_DISK_GB, OLLAMA_DEFAULT_MODEL
from memory_setup.pipeline_runner import PipelineManager, get_archive_metrics
from memory_setup.setup.checker import (
    check_disk_space,
    check_tailscale_status,
    estimate_directory_bytes,
    get_lan_ip,
    run_diagnostics,
)
from memory_setup.setup.installer import (
    build_biometrics_install_command,
    ensure_ollama_running,
    pull_ollama_model,
)
from memory_setup.viewer.builder import (
    list_person_clusters,
    merge_person_clusters,
    quarantine_homework_files,
    rename_person_clusters,
    run_stage5_museum,
)
from memory_setup.viewer.template.server import MuseumHandler

STATIC_DIR = Path(__file__).resolve().parent / "static"

_MIME_OVERRIDES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


def _guess_mime(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    if ext in _MIME_OVERRIDES:
        return _MIME_OVERRIDES[ext]
    ctype, _ = mimetypes.guess_type(str(file_path))
    return ctype or "application/octet-stream"


def _parse_byte_range(range_header: str, file_size: int) -> Tuple[str, int, int]:
    """Parse an HTTP Range header. Returns ('none'|'ok'|'invalid', start, end)."""
    if not range_header or not range_header.lower().startswith("bytes="):
        return "none", 0, 0
    spec = range_header[6:].strip()
    if not spec or "," in spec or "-" not in spec:
        return "none", 0, 0
    if file_size <= 0:
        return "invalid", 0, 0
    start_s, end_s = [p.strip() for p in spec.split("-", 1)]
    if not start_s and not end_s:
        return "invalid", 0, 0
    try:
        if not start_s:
            suffix_len = int(end_s)
            if suffix_len <= 0:
                return "invalid", 0, 0
            start = max(0, file_size - suffix_len)
            end = file_size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else file_size - 1
    except ValueError:
        return "invalid", 0, 0
    end = min(end, file_size - 1)
    if start < 0 or start >= file_size or end < start:
        return "invalid", 0, 0
    return "ok", start, end


def _resolve_content_media(arch_root: Path, raw_path: str) -> Path:
    """Resolve a relative viewer/archive media path and enforce strict containment inside `content/`."""
    rel = str(raw_path or "").strip()
    if not rel:
        raise ValueError("Missing media path")
    if rel.startswith("/archive/"):
        rel = rel[len("/archive/") :]
    arch_resolved = arch_root.resolve()
    viewer_dir = arch_resolved / "viewer"
    if rel.startswith("../"):
        candidate = (viewer_dir / rel).resolve()
    else:
        candidate = (arch_resolved / rel.lstrip("/")).resolve()
    content_root = (arch_resolved / "content").resolve()
    if os.path.commonpath([str(content_root), str(candidate)]) != str(content_root) or candidate == content_root:
        raise PermissionError("Path is outside archive content/ directory")
    return candidate


def _open_with_os_player(target: Path) -> None:
    """Launch a media file in the host operating system's default player."""
    sys_name = platform.system()
    if sys_name == "Darwin":
        subprocess.Popen(["open", str(target)])
    elif sys_name == "Windows":
        os.startfile(str(target))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(target)])


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

    wv = sys.modules.get("webview")
    if wv is not None and getattr(wv, "windows", None):
        try:
            dialog_type = getattr(getattr(wv, "FileDialog", None), "FOLDER", 20)
            result = wv.windows[0].create_file_dialog(dialog_type)
            if result:
                return str(result[0]).rstrip("/\\") or str(result[0])
            return None
        except Exception:
            pass

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        selected = filedialog.askdirectory(title="Select Archive or Raw Media Folder")
        root.destroy()
        return selected or None
    except Exception:
        return None


class _SharedMuseumHTTPServer(socketserver.ThreadingTCPServer):
    """Read-only password-protected museum server bound separately for family sharing."""

    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False


class MuseumShareManager:
    """Manage password configuration and optional background Museum Viewer sharing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._httpd: Optional[_SharedMuseumHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._archive_root: Optional[Path] = None
        self._port: int = 8080

    @staticmethod
    def config_path(archive_root: Path) -> Path:
        return (Path(archive_root).expanduser().resolve() / "viewer" / "share_config.json").resolve()

    def read_config(self, archive_root: Optional[Path]) -> Dict[str, Any]:
        if archive_root is None:
            return {"password": "", "port": 8080}
        cfg_file = self.config_path(archive_root)
        if not cfg_file.is_file():
            return {"password": "", "port": 8080}
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"password": "", "port": 8080}
            return {
                "password": str(data.get("password") or ""),
                "port": int(data.get("port") or 8080),
            }
        except Exception:
            return {"password": "", "port": 8080}

    def save_config(
        self,
        archive_root: Path,
        password: Optional[str] = None,
        port: Optional[int] = None,
    ) -> Dict[str, Any]:
        arch_resolved = Path(archive_root).expanduser().resolve()
        cfg_file = self.config_path(arch_resolved)
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        current = self.read_config(arch_resolved)

        if password is not None:
            current["password"] = str(password)
        if port is not None:
            port_val = int(port)
            if port_val != 0 and not (1024 <= port_val <= 65535):
                raise ValueError("Port must be between 1024 and 65535.")
            current["port"] = port_val or 8080

        cfg_file.write_text(json.dumps(current, indent=2), encoding="utf-8")
        try:
            os.chmod(cfg_file, 0o600)
        except OSError:
            pass

        should_stop = False
        with self._lock:
            if self._httpd is not None and self._archive_root == arch_resolved:
                if current["password"]:
                    self._httpd.museum_password = current["password"]  # type: ignore[attr-defined]
                    self._httpd.museum_session_secret = secrets.token_bytes(32)  # type: ignore[attr-defined]
                else:
                    should_stop = True

        if should_stop:
            self.stop()
        return {"has_password": bool(current["password"]), "port": current["port"]}

    def start(
        self,
        archive_root: Path,
        bind_host: str = "0.0.0.0",
        port: Optional[int] = None,
        password: Optional[str] = None,
    ) -> Dict[str, Any]:
        arch_resolved = Path(archive_root).expanduser().resolve()
        viewer_dir = arch_resolved / "viewer"
        if not viewer_dir.exists() or not (viewer_dir / "viewer.html").exists():
            raise ValueError("Museum Viewer has not been built for this folder yet. Run Stage 5 / Build Museum first.")

        if password is not None:
            self.save_config(arch_resolved, password=password, port=port)
        elif port is not None:
            self.save_config(arch_resolved, port=port)

        cfg = self.read_config(arch_resolved)
        active_pwd = str(cfg.get("password") or "")
        if not active_pwd:
            raise ValueError("A Museum Password is required before starting remote sharing.")

        target_port = int(port) if port is not None else int(cfg.get("port") or 8080)
        self.stop()

        httpd = _SharedMuseumHTTPServer((bind_host, target_port), MuseumHandler)
        httpd.root_dir = arch_resolved  # type: ignore[attr-defined]
        httpd.db_path = (viewer_dir / "catalog.sqlite").resolve()  # type: ignore[attr-defined]
        httpd.museum_password = active_pwd  # type: ignore[attr-defined]
        httpd.museum_read_only = True  # type: ignore[attr-defined]
        httpd.museum_session_secret = secrets.token_bytes(32)  # type: ignore[attr-defined]

        actual_port = int(httpd.server_address[1])
        thread = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.2},
            daemon=True,
        )
        with self._lock:
            self._httpd = httpd
            self._thread = thread
            self._archive_root = arch_resolved
            self._port = actual_port
        thread.start()
        return self.status(arch_resolved)

    def stop(self) -> None:
        with self._lock:
            httpd = self._httpd
            thread = self._thread
            self._httpd = None
            self._thread = None
            self._archive_root = None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def status(self, archive_root: Optional[Path] = None) -> Dict[str, Any]:
        with self._lock:
            running = self._httpd is not None
            serving_root = self._archive_root
            serving_port = self._port

        target_root = Path(archive_root).expanduser().resolve() if archive_root else serving_root
        cfg = self.read_config(target_root)
        active_port = serving_port if running else int(cfg.get("port") or 8080)

        ts = check_tailscale_status()
        lan_ip = get_lan_ip()
        ts_ipv4 = ts.get("ipv4")
        ts_dns = ts.get("dns_name")

        return {
            "running": running,
            "serving_archive": str(serving_root) if serving_root else "",
            "port": active_port,
            "has_password": bool(cfg.get("password")),
            "read_only": True,
            "tailscale": ts,
            "lan_ip": lan_ip,
            "urls": {
                "tailscale": f"http://{ts_ipv4}:{active_port}/viewer/viewer.html" if ts_ipv4 else None,
                "tailscale_dns": f"http://{ts_dns}:{active_port}/viewer/viewer.html" if ts_dns else None,
                "lan": f"http://{lan_ip}:{active_port}/viewer/viewer.html" if lan_ip else None,
                "local": f"http://127.0.0.1:{active_port}/viewer/viewer.html",
            },
        }


class AppState:
    def __init__(self, default_archive: Optional[Path] = None) -> None:
        self.manager = PipelineManager()
        self.share_manager = MuseumShareManager()
        self.selected_archive: Optional[Path] = (
            Path(default_archive).expanduser().resolve() if default_archive else None
        )
        if self.selected_archive:
            self.manager.active_ctx = ArchiveContext(self.selected_archive)
        self.pull_status: Dict[str, Any] = {"running": False, "status": "idle"}
        self.bio_install_status: Dict[str, Any] = {"running": False, "status": "idle"}

    def snapshot(self) -> Dict[str, Any]:
        status = self.manager.get_status()
        status["pull"] = self.pull_status
        status["bio_install"] = self.bio_install_status
        status["selected_archive"] = str(self.selected_archive) if self.selected_archive else ""
        if self.selected_archive and "archive" not in status:
            status["archive"] = get_archive_metrics(self.selected_archive)
        return status


class _DaemonThreadingHTTPServer(socketserver.ThreadingTCPServer):
    """Multi-threaded HTTP server whose request threads never block interpreter shutdown."""

    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(self, server_address: tuple[str, int], RequestHandlerClass: Any, app_state: AppState) -> None:
        self.shutdown_event = threading.Event()
        self.app_state = app_state
        super().__init__(server_address, RequestHandlerClass)

    def server_close(self) -> None:
        self.shutdown_event.set()
        super().server_close()


def create_app_server(
    default_archive: Optional[Path] = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> _DaemonThreadingHTTPServer:
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

        def _serve_file(self, file_path: Path, head_only: bool = False) -> None:
            if not file_path.exists() or not file_path.is_file():
                self.send_error(404, "File not found")
                return
            ctype = _guess_mime(file_path)
            size = file_path.stat().st_size
            range_status, start, end = _parse_byte_range(self.headers.get("Range", ""), size)

            if range_status == "invalid":
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if range_status == "ok":
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(length))
                self.end_headers()
            else:
                start = 0
                length = size
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(size))
                self.end_headers()

            if head_only:
                return

            try:
                remaining = length
                with open(file_path, "rb") as fp:
                    if start > 0:
                        fp.seek(start)
                    while remaining > 0:
                        chunk = fp.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def _handle_static_or_archive(self, head_only: bool = False) -> bool:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == "/" or path == "/index.html":
                self._serve_file(STATIC_DIR / "index.html", head_only=head_only)
                return True
            if path in ("/app.js", "/style.css", "/icon.png", "/icon.ico"):
                self._serve_file(STATIC_DIR / path.lstrip("/"), head_only=head_only)
                return True

            if path.startswith("/archive/") and state.selected_archive is not None:
                rel = urllib.parse.unquote(path[len("/archive/") :]).lstrip("/")
                target = (state.selected_archive / rel).resolve()
                root_resolved = state.selected_archive.resolve()
                if os.path.commonpath([str(root_resolved), str(target)]) == str(root_resolved):
                    self._serve_file(target, head_only=head_only)
                    return True
                self.send_error(403, "Forbidden")
                return True

            return False

        def do_HEAD(self) -> None:
            if self._handle_static_or_archive(head_only=True):
                return
            self.send_error(404, "Not found")

        def _read_json_body(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            return json.loads(raw.decode("utf-8")) if raw else {}

        def _resolve_archive_context(self, raw_path: Optional[str] = None) -> Optional[ArchiveContext]:
            candidate = raw_path or (str(state.selected_archive) if state.selected_archive else None)
            if not candidate:
                return None
            arch_path = Path(candidate).expanduser().resolve()
            state.selected_archive = arch_path
            return ArchiveContext(arch_path)

        def do_GET(self) -> None:
            if self._handle_static_or_archive(head_only=False):
                return

            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == "/__museum_logout":
                self.send_response(303)
                self.send_header("Set-Cookie", "museum_session=; Path=/; Max-Age=0; SameSite=Lax; HttpOnly")
                self.send_header("Location", "/archive/viewer/viewer.html")
                self.end_headers()
                return

            if path == "/api/status":
                self._send_json(200, state.snapshot())
                return

            if path == "/api/share/status":
                qs = urllib.parse.parse_qs(parsed.query)
                raw_path = (qs.get("archive_path") or [None])[0]
                ctx = self._resolve_archive_context(raw_path)
                arch_root = ctx.archive_root if ctx is not None else state.selected_archive
                self._send_json(200, state.share_manager.status(arch_root))
                return

            if path == "/api/people/clusters":
                qs = urllib.parse.parse_qs(parsed.query)
                raw_path = (qs.get("archive_path") or [None])[0]
                ctx = self._resolve_archive_context(raw_path)
                if ctx is None:
                    self._send_json(400, {"error": "No archive selected."})
                    return
                self._send_json(200, {"ok": True, "clusters": list_person_clusters(ctx)})
                return

            if path == "/api/pipeline/events":
                self.close_connection = True
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                shutdown_event: Optional[threading.Event] = getattr(
                    self.server, "shutdown_event", None
                )
                try:
                    while shutdown_event is None or not shutdown_event.is_set():
                        payload = json.dumps(state.snapshot())
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        if shutdown_event is not None:
                            if shutdown_event.wait(0.8):
                                break
                        else:
                            time.sleep(0.8)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
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
                target = body.get("target", "archive")
                if chosen and target == "archive":
                    state.selected_archive = Path(chosen).resolve()
                    state.manager.active_ctx = ArchiveContext(state.selected_archive)
                self._send_json(200, {"path": chosen, "target": target})
                return

            if path == "/api/preflight":
                raw_path = body.get("archive_path") or (str(state.selected_archive) if state.selected_archive else None)
                mode = body.get("mode", "in_place")
                raw_sources = body.get("source_dirs") or []
                src_dirs = [Path(s).expanduser().resolve() for s in raw_sources if str(s).strip()]
                arch_path = Path(raw_path).expanduser().resolve() if raw_path else None
                if arch_path:
                    state.selected_archive = arch_path
                    state.manager.active_ctx = ArchiveContext(arch_path, source_dirs=src_dirs, mode=mode)
                diag = run_diagnostics(archive_path=arch_path, mode=mode)
                if mode == "safe_copy" and arch_path and src_dirs:
                    src_gb = sum(estimate_directory_bytes(s) for s in src_dirs if s.exists()) / (1024 ** 3)
                    free_gb = check_disk_space(arch_path)
                    req_gb = src_gb + MIN_FREE_DISK_GB
                    diag["disk"] = {
                        **diag["disk"],
                        "source_gb": round(src_gb, 2),
                        "required_gb": round(req_gb, 2),
                        "ok": free_gb >= req_gb,
                        "message": f"{free_gb:.2f} GiB free (needs {req_gb:.2f} GiB incl. {src_gb:.2f} GiB copy)",
                    }
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

            if path == "/api/setup/install-biometrics":
                if not state.bio_install_status.get("running"):
                    state.bio_install_status = {"running": True, "status": "Installing InsightFace, ONNX Runtime & OpenCV..."}

                    def _bio_worker() -> None:
                        try:
                            cmd = build_biometrics_install_command(sys.executable)
                            proc = subprocess.run(
                                cmd,
                                capture_output=True,
                                text=True,
                                timeout=600,
                            )
                            if proc.returncode == 0:
                                state.bio_install_status = {"running": False, "ok": True, "status": "Installed successfully!"}
                            else:
                                err = (proc.stderr or proc.stdout or "Install failed").strip().splitlines()[-1]
                                state.bio_install_status = {"running": False, "ok": False, "status": err}
                        except Exception as exc:
                            state.bio_install_status = {"running": False, "ok": False, "status": str(exc)}

                    threading.Thread(target=_bio_worker, daemon=True).start()
                self._send_json(200, {"ok": True, "bio_install": state.bio_install_status})
                return

            if path == "/api/pipeline/start":
                raw_path = body.get("archive_path") or (str(state.selected_archive) if state.selected_archive else None)
                if not raw_path:
                    self._send_json(400, {"error": "No archive folder selected."})
                    return
                arch_path = Path(raw_path).expanduser().resolve()
                mode = body.get("mode", "in_place")
                raw_sources = body.get("source_dirs") or []
                src_dirs = [Path(s).expanduser().resolve() for s in raw_sources if str(s).strip()]

                if not body.get("force", False):
                    free_gb = check_disk_space(arch_path)
                    req_gb = MIN_FREE_DISK_GB
                    if mode == "safe_copy" and src_dirs:
                        req_gb += sum(estimate_directory_bytes(s) for s in src_dirs if s.exists()) / (1024 ** 3)
                    if free_gb < req_gb:
                        self._send_json(
                            400,
                            {
                                "error": f"Disk safety check blocked launch ({free_gb:.2f} GiB free, needs {req_gb:.2f} GiB)."
                            },
                        )
                        return

                state.selected_archive = arch_path
                ctx = ArchiveContext(
                    archive_root=arch_path,
                    source_dirs=src_dirs,
                    mode=mode,
                    speed_mode=body.get("speed_mode", "whisper"),
                    enable_biometrics=bool(body.get("enable_biometrics", True)),
                    enable_vision=bool(body.get("enable_vision", True)),
                    reprocess=bool(body.get("reprocess", False)),
                    recluster=bool(body.get("recluster", False)),
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
                ctx = self._resolve_archive_context(body.get("archive_path"))
                if ctx is None:
                    self._send_json(400, {"error": "No archive selected."})
                    return
                res = run_stage5_museum(ctx)
                self._send_json(200, {"ok": True, "result": res, "archive": get_archive_metrics(ctx.archive_root)})
                return

            if path == "/api/people/rename":
                ctx = self._resolve_archive_context(body.get("archive_path"))
                if ctx is None:
                    self._send_json(400, {"error": "No archive selected."})
                    return
                count = rename_person_clusters(ctx, body.get("updates", []))
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "updated": count,
                        "clusters": list_person_clusters(ctx),
                        "archive": get_archive_metrics(ctx.archive_root),
                    },
                )
                return

            if path == "/api/people/merge":
                ctx = self._resolve_archive_context(body.get("archive_path"))
                if ctx is None:
                    self._send_json(400, {"error": "No archive selected."})
                    return
                try:
                    source_id = int(body["source_id"])
                    target_id = int(body["target_id"])
                    merge_person_clusters(ctx, source_id, target_id)
                except (KeyError, TypeError, ValueError) as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "clusters": list_person_clusters(ctx),
                        "archive": get_archive_metrics(ctx.archive_root),
                    },
                )
                return

            if path == "/api/homework/quarantine":
                ctx = self._resolve_archive_context(body.get("archive_path"))
                if ctx is None:
                    self._send_json(400, {"error": "No archive selected."})
                    return
                moved = quarantine_homework_files(ctx)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "moved_count": moved,
                        "archive": get_archive_metrics(ctx.archive_root),
                    },
                )
                return

            if path == "/api/open-media":
                raw_arch = body.get("archive_path") or (str(state.selected_archive) if state.selected_archive else None)
                if not raw_arch:
                    self._send_json(400, {"error": "No archive selected."})
                    return
                arch_root = Path(raw_arch).expanduser().resolve()
                try:
                    target = _resolve_content_media(arch_root, body.get("path", ""))
                except PermissionError as exc:
                    self._send_json(403, {"error": str(exc)})
                    return
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                if not target.is_file():
                    self._send_json(404, {"error": "Media file not found"})
                    return
                _open_with_os_player(target)
                self._send_json(200, {"ok": True, "opened": target.name})
                return

            if path == "/api/share/config":
                ctx = self._resolve_archive_context(body.get("archive_path"))
                if ctx is None:
                    self._send_json(400, {"error": "Select an archive folder first."})
                    return
                raw_port = body.get("port")
                port_val: Optional[int] = None
                if raw_port not in (None, ""):
                    try:
                        port_val = int(raw_port)
                    except (TypeError, ValueError):
                        self._send_json(400, {"error": "Share port must be a valid integer."})
                        return
                pwd_arg = body["password"] if "password" in body else None
                try:
                    info = state.share_manager.save_config(
                        ctx.archive_root, password=pwd_arg, port=port_val
                    )
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                self._send_json(200, {"ok": True, "share": info})
                return

            if path == "/api/share/start":
                ctx = self._resolve_archive_context(body.get("archive_path"))
                if ctx is None:
                    self._send_json(400, {"error": "Select an archive folder first."})
                    return
                raw_port = body.get("port")
                port_val = None
                if raw_port not in (None, ""):
                    try:
                        port_val = int(raw_port)
                    except (TypeError, ValueError):
                        self._send_json(400, {"error": "Share port must be a valid integer."})
                        return
                bind_host = str(body.get("bind_host") or "0.0.0.0").strip() or "0.0.0.0"
                pwd_arg = body["password"] if "password" in body else None
                try:
                    info = state.share_manager.start(
                        ctx.archive_root,
                        bind_host=bind_host,
                        port=port_val,
                        password=pwd_arg,
                    )
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                self._send_json(200, {"ok": True, "share": info})
                return

            if path == "/api/share/stop":
                ctx = self._resolve_archive_context(body.get("archive_path"))
                state.share_manager.stop()
                arch_root = ctx.archive_root if ctx is not None else state.selected_archive
                self._send_json(200, {"ok": True, "share": state.share_manager.status(arch_root)})
                return

            if path == "/api/share/tailscale-open":
                ts = check_tailscale_status()
                sys_name = platform.system()
                opened = False
                msg = ""
                if sys_name == "Darwin":
                    app_bundle = Path("/Applications/Tailscale.app")
                    if app_bundle.exists():
                        subprocess.Popen(["open", "-a", str(app_bundle)])
                        opened = True
                        msg = "Opened Tailscale menu bar app. Toggle Connect or Sign In there if needed."
                    else:
                        msg = "Tailscale.app was not found in /Applications. Install Tailscale for macOS first."
                elif sys_name == "Windows":
                    gui_exe = Path(r"C:\Program Files\Tailscale\tailscale-ipn.exe")
                    if gui_exe.exists():
                        subprocess.Popen([str(gui_exe)])
                        opened = True
                        msg = "Opened Tailscale tray app."
                    else:
                        msg = "Tailscale Windows app was not found. Install Tailscale for Windows first."
                else:
                    msg = (
                        "Tailscale binary detected; use your Tailscale desktop/tray app to connect."
                        if ts.get("installed")
                        else "Tailscale not found on this machine. Install it from tailscale.com/download."
                    )
                self._send_json(200, {"ok": opened, "opened": opened, "message": msg, "tailscale": ts})
                return

            self._send_json(404, {"error": "Unknown API endpoint"})

    for candidate_port in range(port, port + 20):
        try:
            return _DaemonThreadingHTTPServer((host, candidate_port), ControlCenterHandler, app_state=state)
        except OSError:
            continue
    return _DaemonThreadingHTTPServer((host, 0), ControlCenterHandler, app_state=state)


def get_window_icon_path(system_name: Optional[str] = None) -> Optional[Path]:
    """Return the native window icon path (.ico on Windows WinForms, .png on macOS/Linux)."""
    sys_name = system_name or platform.system()
    filename = "icon.ico" if sys_name == "Windows" else "icon.png"
    candidate = STATIC_DIR / filename
    return candidate if candidate.exists() else None


def _shutdown_control_center(
    server: _DaemonThreadingHTTPServer,
    wait_pipeline_seconds: float = 2.0,
) -> None:
    """Stop SSE streams, signal any active pipeline to pause safely, and close HTTP sockets."""
    if getattr(server, "_did_shutdown", False):
        return
    server._did_shutdown = True
    server.shutdown_event.set()
    server.app_state.share_manager.stop()
    manager = server.app_state.manager
    if manager.get_status().get("running"):
        manager.stop()
        worker = getattr(manager, "_thread", None)
        if worker is not None and worker.is_alive():
            worker.join(timeout=wait_pipeline_seconds)
    server.shutdown()
    server.server_close()


def _configure_macos_app_identity() -> None:
    """Set macOS Dock icon and menu bar app title even when invoked directly via python."""
    if platform.system() != "Darwin":
        return
    try:
        import AppKit  # type: ignore
        import Foundation  # type: ignore

        bundle = AppKit.NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = "Memory Setup"
            info["CFBundleDisplayName"] = "Memory Setup"

        Foundation.NSProcessInfo.processInfo().setProcessName_("Memory Setup")
        app = AppKit.NSApplication.sharedApplication()
        icon_path = STATIC_DIR / "icon.png"
        if icon_path.exists():
            image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(icon_path))
            if image is not None:
                app.setApplicationIconImage_(image)
    except Exception:
        pass


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
        except ImportError:
            webview = None

        if webview is not None:
            _configure_macos_app_identity()
            srv_thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.2},
                daemon=True,
            )
            srv_thread.start()
            window = webview.create_window(
                "Memory Setup — Local Media Intelligence",
                url,
                width=1240,
                height=840,
            )

            def _on_closing() -> None:
                server.shutdown_event.set()
                if server.app_state.manager.get_status().get("running"):
                    server.app_state.manager.stop()

            def _on_closed() -> None:
                server.shutdown_event.set()

            window.events.closing += _on_closing
            window.events.closed += _on_closed

            icon_file = get_window_icon_path()
            start_kwargs: Dict[str, Any] = {}
            if icon_file is not None:
                start_kwargs["icon"] = str(icon_file)

            try:
                webview.start(**start_kwargs)
                _shutdown_control_center(server)
                return
            except Exception as exc:
                server.shutdown()
                print(
                    f"Native webview unavailable ({exc}); falling back to default browser.",
                    file=sys.stderr,
                )

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
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nMemory Setup stopped cleanly.")
    finally:
        _shutdown_control_center(server)
