#!/usr/bin/env python3
"""Read-only HTTP Range streaming and synced catalog server for Offline Museum Viewer."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import html
import http.cookies
import http.server
import json
import mimetypes
import os
import platform
import secrets
import shutil
import socketserver
import sqlite3
import subprocess
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from memory_setup.config import ArchiveContext as _ArchiveContext
    from memory_setup.viewer import builder as _builder
except ImportError:
    _ArchiveContext = None  # type: ignore[assignment]
    _builder = None  # type: ignore[assignment]


CUR_DIR = Path(__file__).resolve().parent
ROOT_DIR = CUR_DIR.parent if CUR_DIR.name == "viewer" else CUR_DIR
VIEWER_DIR = ROOT_DIR / "viewer"
DB_PATH = VIEWER_DIR / "catalog.sqlite"

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
    """Parse HTTP Range header. Returns ('none'|'ok'|'invalid', start, end)."""
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


def _resolve_content_media(root_dir: Path, raw_path: str) -> Path:
    """Resolve media path and enforce strict containment inside `root_dir/content/`."""
    rel = str(raw_path or "").strip()
    if not rel:
        raise ValueError("Missing media path")
    if rel.startswith("/archive/"):
        rel = rel[len("/archive/") :]
    arch_resolved = root_dir.resolve()
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
    """Open media file in the host operating system's default application."""
    sys_name = platform.system()
    if sys_name == "Darwin":
        subprocess.Popen(["open", str(target)])
    elif sys_name == "Windows":
        os.startfile(str(target))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(target)])


def _to_viewer_web_path(rel_path: str) -> str:
    if rel_path.startswith("viewer/"):
        return rel_path[len("viewer/") :]
    if rel_path.startswith("content/"):
        return "../" + rel_path
    return rel_path


def _refresh_catalog_data_js(root_dir: Optional[Path] = None, db_path: Optional[Path] = None) -> None:
    resolved_root = (root_dir or ROOT_DIR).resolve()
    viewer_dir = resolved_root / "viewer"
    target_db = (db_path or (viewer_dir / "catalog.sqlite")).resolve()
    if not target_db.exists():
        return
    if _builder is not None and _ArchiveContext is not None and target_db == (viewer_dir / "catalog.sqlite").resolve():
        ctx = _ArchiveContext(resolved_root)
        conn = sqlite3.connect(str(target_db), timeout=60.0)
        _builder.export_catalog_data_js(ctx, conn)
        conn.close()
        return

    conn = sqlite3.connect(str(target_db), timeout=60.0)
    cursor = conn.cursor()
    rows = cursor.execute(
        """
        SELECT
            m.id,
            p.processing_path,
            m.current_path,
            COALESCE(m.capture_timestamp, ''),
            COALESCE(e.city, ''),
            COALESCE(e.country, ''),
            COALESCE(a.short_caption, ''),
            COALESCE(a.is_document_or_homework, 0),
            COALESCE(e.model, '')
        FROM media_items m
        JOIN processing_inputs p ON m.id = p.media_id
        LEFT JOIN metadata_exif e ON m.id = e.media_id
        LEFT JOIN ai_descriptions a ON m.id = a.media_id
        WHERE m.media_type = 'photo'
        ORDER BY m.capture_timestamp DESC
        """
    ).fetchall()

    face_clusters = {}
    for mid, cid in cursor.execute(
        "SELECT DISTINCT media_id, cluster_id FROM people_faces WHERE cluster_id >= 0"
    ).fetchall():
        face_clusters.setdefault(mid, []).append(cid)

    items = []
    for r in rows:
        mid, proc, cur = r[0], r[1], r[2]
        p_web = _to_viewer_web_path(proc)
        orig_web = _to_viewer_web_path(cur)
        loc = f"{r[4]}, {r[5]}" if r[4] else ""
        items.append(
            {
                "id": mid,
                "p": p_web,
                "orig": orig_web,
                "d": r[3][:10] if r[3] else "",
                "l": loc,
                "c": r[6],
                "hw": bool(r[7]),
                "cam": r[8],
                "f": face_clusters.get(mid, []),
            }
        )

    locations = [
        {"city": r[0], "country": r[1], "lat": round(r[2], 5), "lon": round(r[3], 5), "count": r[4]}
        for r in cursor.execute(
            """
            SELECT city, country, AVG(gps_latitude), AVG(gps_longitude), COUNT(*)
            FROM metadata_exif
            WHERE gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL AND city IS NOT NULL
            GROUP BY city, country
            ORDER BY COUNT(*) DESC
            LIMIT 35
            """
        ).fetchall()
    ]

    clust_rows = cursor.execute(
        """
        SELECT cluster_id, COUNT(*), MAX(person_name)
        FROM people_faces
        WHERE cluster_id >= 0
        GROUP BY cluster_id
        ORDER BY COUNT(*) DESC
        LIMIT 30
        """
    ).fetchall()
    core_clusters = [
        {"id": r[0], "count": r[1], "name": r[2] or f"Family Member {r[0] + 1}"}
        for r in clust_rows[:9]
    ]
    extended_clusters = [
        {"id": r[0], "count": r[1], "name": r[2] or f"Recurring Person {r[0] + 1}"}
        for r in clust_rows[9:30]
    ]

    video_rows = cursor.execute(
        """
        SELECT
            id,
            current_path,
            COALESCE(capture_timestamp, ''),
            LOWER(COALESCE(original_ext, '')),
            COALESCE(file_size_bytes, 0),
            COALESCE(original_filename, current_filename, ''),
            COALESCE(companion_role, 'none')
        FROM media_items
        WHERE media_type = 'video'
        ORDER BY COALESCE(capture_timestamp, '') DESC, id DESC
        """
    ).fetchall()
    videos = []
    for vr in video_rows:
        mid, cur_p, ts, ext, sz, orig_fn, role = vr
        ext_clean = ext if not ext or ext.startswith(".") else f".{ext}"
        videos.append(
            {
                "id": mid,
                "src": _to_viewer_web_path(cur_p),
                "d": ts[:10] if ts else "",
                "ext": ext_clean,
                "sz": int(sz or 0),
                "fn": orig_fn or Path(cur_p).name,
                "live": role == "live_photo_video",
            }
        )
    conn.close()

    data_js_path = viewer_dir / "catalog_data.js"
    with open(data_js_path, "w", encoding="utf-8") as fp:
        fp.write("// Auto-generated Catalog Data for Offline Museum Viewer\n")
        fp.write(f"const ALL_PHOTOS = {json.dumps(items)};\n\n")
        fp.write(f"const ALL_VIDEOS = {json.dumps(videos)};\n\n")
        fp.write(f"const MAP_LOCATIONS = {json.dumps(locations)};\n\n")
        fp.write(f"const CORE_CLUSTERS = {json.dumps(core_clusters)};\n\n")
        fp.write(f"const EXTENDED_CLUSTERS = {json.dumps(extended_clusters)};\n")


def _load_share_config(root_dir: Path) -> Dict[str, Any]:
    cfg_path = root_dir.resolve() / "viewer" / "share_config.json"
    if not cfg_path.is_file():
        return {}
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_auth_password(server: Any) -> str:
    explicit = getattr(server, "museum_password", None)
    if explicit is not None:
        return str(explicit)
    env_pwd = str(os.environ.get("MUSEUM_PASSWORD", "") or "")
    if env_pwd:
        return env_pwd
    root = Path(getattr(server, "root_dir", ROOT_DIR)).resolve()
    return str(_load_share_config(root).get("password") or "")


def _get_session_secret(server: Any) -> bytes:
    secret = getattr(server, "museum_session_secret", None)
    if not isinstance(secret, (bytes, bytearray)) or len(secret) < 16:
        secret = secrets.token_bytes(32)
        server.museum_session_secret = secret
    return bytes(secret)


def _compute_session_token(secret: bytes, password: str) -> str:
    return hmac.new(secret, b"museum-session-v1:" + password.encode("utf-8"), hashlib.sha256).hexdigest()


def _sanitize_next_path(raw: str) -> str:
    val = str(raw or "").strip()
    if not val or not val.startswith("/") or val.startswith("//") or "\r" in val or "\n" in val:
        return "/viewer/viewer.html"
    if val in ("/", "/__museum_login"):
        return "/viewer/viewer.html"
    return val


def _render_login_html(next_path: str, error_msg: str = "") -> bytes:
    escaped_next = html.escape(_sanitize_next_path(next_path), quote=True)
    err_block = (
        f'<div class="err" role="alert">{html.escape(error_msg)}</div>'
        if error_msg
        else ""
    )
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Family Museum Access — Password Required</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      min-height: 100vh; display: grid; place-items: center; padding: 24px;
      background: radial-gradient(900px 500px at 50% -10%, rgba(99, 102, 241, 0.16), transparent 60%), #070b14;
      color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
    }}
    .card {{
      width: min(420px, 100%); background: #111827; border: 1px solid rgba(148,163,184,0.22);
      border-radius: 16px; padding: 28px; box-shadow: 0 24px 60px rgba(0,0,0,0.65);
    }}
    .badge {{
      display: inline-block; font-size: 11px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase;
      color: #a5b4fc; background: rgba(99,102,241,0.16); border: 1px solid rgba(99,102,241,0.35);
      border-radius: 999px; padding: 3px 10px; margin-bottom: 12px;
    }}
    h1 {{ font-size: 1.25rem; font-weight: 700; margin-bottom: 6px; }}
    p {{ font-size: 0.86rem; color: #94a3b8; line-height: 1.5; margin-bottom: 18px; }}
    label {{ display: block; font-size: 0.78rem; font-weight: 600; color: #cbd5e1; margin-bottom: 6px; }}
    input[type="password"] {{
      width: 100%; padding: 11px 13px; border-radius: 8px; background: #0a101d;
      border: 1px solid rgba(148,163,184,0.3); color: #f1f5f9; font-size: 0.95rem; margin-bottom: 14px;
    }}
    input[type="password"]:focus {{ outline: none; border-color: #6366f1; box-shadow: 0 0 0 3px rgba(99,102,241,0.22); }}
    button {{
      width: 100%; padding: 11px 16px; border: 0; border-radius: 8px; cursor: pointer; font-weight: 600;
      font-size: 0.92rem; color: #ffffff; background: linear-gradient(135deg, #6366f1, #3b82f6);
    }}
    button:hover {{ filter: brightness(1.08); }}
    .err {{
      margin-bottom: 14px; padding: 9px 12px; border-radius: 8px; font-size: 0.82rem;
      background: rgba(244,63,94,0.14); border: 1px solid rgba(244,63,94,0.38); color: #fda4af;
    }}
  </style>
</head>
<body>
  <form class="card" method="POST" action="/__museum_login">
    <span class="badge">Private Family Archive</span>
    <h1>Offline Museum Viewer</h1>
    <p>Enter the museum access password to browse photos, videos, map pins, and memories.</p>
    {err_block}
    <input type="hidden" name="next" value="{escaped_next}">
    <label for="museumPassword">Access Password</label>
    <input id="museumPassword" name="password" type="password" autocomplete="current-password" autofocus required placeholder="Enter museum password">
    <button type="submit">Unlock Museum</button>
  </form>
</body>
</html>"""
    return doc.encode("utf-8")


def _search_catalog(db_path: Path, query: str, limit: int = 50) -> List[Dict[str, Any]]:
    clean_q = str(query or "").strip()
    if not clean_q or not db_path.exists():
        return []
    safe_limit = max(1, min(int(limit or 50), 200))
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=30.0)
    try:
        tokens = [t.strip() for t in clean_q.split() if t.strip()]
        fts_expr = " ".join('"' + t.replace('"', '""') + '"*' for t in tokens)
        rows = []
        try:
            rows = conn.execute(
                """
                SELECT
                    f.media_id,
                    f.media_type,
                    f.filename,
                    f.short_caption,
                    f.person_names,
                    f.city,
                    f.country,
                    f.capture_timestamp,
                    m.current_path
                FROM ai_search_fts f
                JOIN media_items m ON m.id = f.media_id
                WHERE ai_search_fts MATCH ?
                ORDER BY m.capture_timestamp DESC
                LIMIT ?
                """,
                (fts_expr, safe_limit),
            ).fetchall()
        except sqlite3.OperationalError:
            like_q = f"%{clean_q}%"
            rows = conn.execute(
                """
                SELECT
                    m.id,
                    m.media_type,
                    COALESCE(NULLIF(m.original_filename, ''), m.current_filename, ''),
                    COALESCE(a.short_caption, ''),
                    '',
                    COALESCE(e.city, ''),
                    COALESCE(e.country, ''),
                    COALESCE(NULLIF(m.matched_timestamp, ''), m.capture_timestamp, ''),
                    m.current_path
                FROM media_items m
                LEFT JOIN ai_descriptions a ON m.id = a.media_id
                LEFT JOIN metadata_exif e ON m.id = e.media_id
                WHERE m.media_type IN ('photo', 'video', 'document')
                  AND (
                      m.original_filename LIKE ? OR m.current_filename LIKE ?
                      OR a.short_caption LIKE ? OR a.detailed_description LIKE ?
                  )
                ORDER BY m.capture_timestamp DESC
                LIMIT ?
                """,
                (like_q, like_q, like_q, like_q, safe_limit),
            ).fetchall()

        return [
            {
                "media_id": r[0],
                "media_type": r[1],
                "filename": r[2],
                "short_caption": r[3],
                "person_names": r[4],
                "city": r[5],
                "country": r[6],
                "capture_timestamp": r[7],
                "path": _to_viewer_web_path(r[8]),
            }
            for r in rows
        ]
    finally:
        conn.close()


class MuseumHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _get_roots(self) -> Tuple[Path, Path]:
        root = Path(getattr(self.server, "root_dir", ROOT_DIR)).resolve()
        db_path = Path(getattr(self.server, "db_path", root / "viewer" / "catalog.sqlite"))
        return root, db_path

    def _is_authenticated(self) -> bool:
        pwd = _get_auth_password(self.server)
        if not pwd:
            return True
        raw_cookie = self.headers.get("Cookie", "")
        if raw_cookie:
            cookie = http.cookies.SimpleCookie()
            try:
                cookie.load(raw_cookie)
                morsel = cookie.get("museum_session")
                if morsel is not None:
                    expected = _compute_session_token(_get_session_secret(self.server), pwd)
                    if hmac.compare_digest(morsel.value, expected):
                        return True
            except http.cookies.CookieError:
                pass
        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_hdr[6:].strip()).decode("utf-8")
                supplied = decoded.split(":", 1)[1] if ":" in decoded else decoded
                if hmac.compare_digest(supplied, pwd):
                    return True
            except Exception:
                pass
        return False

    def _send_auth_required(self, error_msg: str = "", head_only: bool = False) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            body = json.dumps({"error": "Authentication required"}).encode("utf-8")
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("WWW-Authenticate", 'Basic realm="Offline Museum Viewer"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return

        next_path = _sanitize_next_path("/viewer/viewer.html" if parsed.path in ("/__museum_login", "/__museum_logout") else self.path)
        body = _render_login_html(next_path, error_msg=error_msg)
        self.send_response(401)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
        self.send_header("WWW-Authenticate", 'Basic realm="Offline Museum Viewer"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _handle_logout(self) -> None:
        self.send_response(303)
        self.send_header("Set-Cookie", "museum_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Location", "/viewer/viewer.html")
        self.end_headers()

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

    def _resolve_static_target(self) -> Optional[Path]:
        root_dir, _ = self._get_roots()
        parsed = urllib.parse.urlparse(self.path)
        rel = urllib.parse.unquote(parsed.path).lstrip("/")
        if not rel:
            rel = "viewer/viewer.html"
        target = (root_dir / rel).resolve()
        if os.path.commonpath([str(root_dir), str(target)]) != str(root_dir):
            return None
        name_low = target.name.lower()
        if (
            name_low == "share_config.json"
            or target.suffix.lower() in {".sqlite", ".db", ".sqlite-wal", ".sqlite-shm"}
            or name_low.endswith(".sqlite")
            or name_low.endswith(".sqlite-wal")
            or name_low.endswith(".sqlite-shm")
        ):
            return None
        if target.is_dir():
            idx = target / "index.html"
            if idx.is_file():
                return idx
        return target

    def do_HEAD(self) -> None:
        if not self._is_authenticated():
            self._send_auth_required(head_only=True)
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/search":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            return
        target = self._resolve_static_target()
        if target is None:
            self.send_error(403, "Forbidden")
            return
        if target.is_dir():
            super().do_HEAD()
            return
        self._serve_file(target, head_only=True)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/__museum_logout":
            self._handle_logout()
            return
        if not self._is_authenticated():
            self._send_auth_required(head_only=False)
            return
        if parsed.path == "/__museum_login":
            self.send_response(303)
            self.send_header("Location", "/viewer/viewer.html")
            self.end_headers()
            return
        if parsed.path == "/api/search":
            qs = urllib.parse.parse_qs(parsed.query)
            q = (qs.get("q") or [""])[0]
            try:
                limit = int((qs.get("limit") or ["50"])[0])
            except ValueError:
                limit = 50
            _, db_path = self._get_roots()
            results = _search_catalog(db_path, q, limit=limit)
            self._send_json(200, {"ok": True, "query": q, "results": results})
            return
        target = self._resolve_static_target()
        if target is None:
            self.send_error(403, "Forbidden")
            return
        if target.is_dir():
            super().do_GET()
            return
        self._serve_file(target, head_only=False)

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_login_post(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b""
        ctype = (self.headers.get("Content-Type") or "").lower()
        is_json = "application/json" in ctype
        next_path = "/viewer/viewer.html"
        supplied = ""
        if is_json:
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
                supplied = str(data.get("password") or "")
                next_path = _sanitize_next_path(str(data.get("next") or next_path))
            except Exception:
                self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                return
        else:
            form = urllib.parse.parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
            supplied = (form.get("password") or [""])[0]
            next_path = _sanitize_next_path((form.get("next") or [next_path])[0])

        pwd = _get_auth_password(self.server)
        if pwd and not hmac.compare_digest(supplied, pwd):
            if is_json:
                self._send_json(401, {"ok": False, "error": "Invalid password"})
            else:
                self._send_auth_required(error_msg="Incorrect museum password. Please try again.")
            return

        cookie_header = None
        if pwd:
            token = _compute_session_token(_get_session_secret(self.server), pwd)
            cookie_header = f"museum_session={token}; Path=/; HttpOnly; SameSite=Lax"

        if is_json:
            body = json.dumps({"ok": True, "next": next_path}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if cookie_header:
                self.send_header("Set-Cookie", cookie_header)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(303)
            if cookie_header:
                self.send_header("Set-Cookie", cookie_header)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Location", next_path)
            self.end_headers()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/__museum_login":
            self._handle_login_post()
            return
        if parsed.path == "/__museum_logout":
            self._handle_logout()
            return

        if not self._is_authenticated():
            self._send_auth_required()
            return

        if getattr(self.server, "museum_read_only", False):
            self._send_json(403, {"error": "Server is running in read-only share mode"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(400, {"error": "Invalid JSON"})
            return

        root_dir, db_path = self._get_roots()

        if parsed.path == "/api/people/rename":
            updates = body.get("updates", [])
            if _builder is not None and _ArchiveContext is not None and db_path.resolve() == (root_dir / "viewer" / "catalog.sqlite").resolve():
                count = _builder.rename_person_clusters(_ArchiveContext(root_dir), updates)
                self._send_json(200, {"ok": True, "updated": count})
                return
            conn = sqlite3.connect(str(db_path), timeout=60.0)
            now_iso = datetime.now(timezone.utc).isoformat()
            for item in updates:
                cid = int(item["cluster_id"])
                clean_name = str(item.get("name") or "").strip() or None
                conn.execute(
                    "UPDATE people_faces SET person_name = ? WHERE cluster_id = ?",
                    (clean_name, cid),
                )
                conn.execute(
                    "INSERT INTO operation_history (media_id, stage, action, from_path, to_path, status, timestamp, details) "
                    "VALUES (NULL, 'viewer_server', 'RENAME_PERSON', NULL, NULL, 'SUCCESS', ?, ?)",
                    (now_iso, json.dumps({"cluster_id": cid, "person_name": clean_name})),
                )
            conn.commit()
            conn.close()
            _refresh_catalog_data_js(root_dir=root_dir, db_path=db_path)
            self._send_json(200, {"ok": True, "updated": len(updates)})
            return

        if parsed.path == "/api/people/merge":
            try:
                src_id = int(body["source_id"])
                tgt_id = int(body["target_id"])
            except (KeyError, TypeError, ValueError):
                self._send_json(400, {"error": "Invalid source_id or target_id"})
                return
            if src_id == tgt_id:
                self._send_json(400, {"error": "Source and target cluster IDs must differ."})
                return
            if _builder is not None and _ArchiveContext is not None and db_path.resolve() == (root_dir / "viewer" / "catalog.sqlite").resolve():
                _builder.merge_person_clusters(_ArchiveContext(root_dir), src_id, tgt_id)
                self._send_json(200, {"ok": True, "source_id": src_id, "target_id": tgt_id})
                return
            conn = sqlite3.connect(str(db_path), timeout=60.0)
            row = conn.execute(
                "SELECT MAX(person_name) FROM people_faces WHERE cluster_id = ?",
                (tgt_id,),
            ).fetchone()
            target_name = row[0] if row and row[0] else None
            if target_name is not None:
                conn.execute(
                    "UPDATE people_faces SET cluster_id = ?, person_name = ? WHERE cluster_id = ?",
                    (tgt_id, target_name, src_id),
                )
            else:
                conn.execute(
                    "UPDATE people_faces SET cluster_id = ? WHERE cluster_id = ?",
                    (tgt_id, src_id),
                )
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO operation_history (media_id, stage, action, from_path, to_path, status, timestamp, details) "
                "VALUES (NULL, 'viewer_server', 'MERGE_PERSON', ?, ?, 'SUCCESS', ?, ?)",
                (str(src_id), str(tgt_id), now_iso, json.dumps({"source_id": src_id, "target_id": tgt_id, "target_name": target_name})),
            )
            conn.commit()
            conn.close()
            _refresh_catalog_data_js(root_dir=root_dir, db_path=db_path)
            self._send_json(200, {"ok": True, "source_id": src_id, "target_id": tgt_id})
            return

        if parsed.path == "/api/homework/quarantine":
            if _builder is not None and _ArchiveContext is not None and db_path.resolve() == (root_dir / "viewer" / "catalog.sqlite").resolve():
                moved = _builder.quarantine_homework_files(_ArchiveContext(root_dir))
                self._send_json(200, {"ok": True, "moved_count": moved})
                return
            archive_dir = root_dir / "content" / "documents" / "homework_archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_resolved = archive_dir.resolve()
            conn = sqlite3.connect(str(db_path), timeout=60.0)
            rows = conn.execute(
                """
                SELECT m.id, m.current_path
                FROM media_items m
                JOIN ai_descriptions a ON m.id = a.media_id
                WHERE a.is_document_or_homework = 1
                """
            ).fetchall()
            moved = 0
            now_iso = datetime.now(timezone.utc).isoformat()
            for mid, cur_rel in rows:
                src = root_dir / cur_rel
                if src.exists() and src.parent.resolve() != archive_resolved:
                    dst = archive_dir / src.name
                    if dst.exists():
                        dst = archive_dir / f"{mid}_{src.name}"
                    shutil.move(str(src), str(dst))
                    new_rel = dst.relative_to(root_dir).as_posix()
                    conn.execute(
                        "UPDATE media_items SET current_path = ?, current_filename = ?, updated_at = ? WHERE id = ?",
                        (new_rel, dst.name, now_iso, mid),
                    )
                    conn.execute(
                        "UPDATE processing_inputs SET processing_path = ? WHERE media_id = ? AND is_reused_original = 1",
                        (new_rel, mid),
                    )
                    conn.execute(
                        "INSERT INTO operation_history (media_id, stage, action, from_path, to_path, status, timestamp, details) "
                        "VALUES (?, 'viewer_server', 'QUARANTINE', ?, ?, 'SUCCESS', ?, NULL)",
                        (mid, cur_rel, new_rel, now_iso),
                    )
                    moved += 1
            conn.commit()
            conn.close()
            _refresh_catalog_data_js(root_dir=root_dir, db_path=db_path)
            self._send_json(200, {"ok": True, "moved_count": moved})
            return

        if parsed.path == "/api/open-media":
            try:
                target = _resolve_content_media(root_dir, body.get("path", ""))
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

        self._send_json(404, {"error": "Unknown API endpoint"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only HTTP server for the Offline Museum Viewer.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1; use 0.0.0.0 for LAN/Tailscale).")
    parser.add_argument("--port", type=int, default=None, help="Bind port (defaults to first open port in 8080-8094).")
    parser.add_argument(
        "--password",
        default=os.environ.get("MUSEUM_PASSWORD", ""),
        help="Optional access password (or set MUSEUM_PASSWORD env var).",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Disable OS file opening and mutation APIs (recommended when sharing over the internet).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open the local web browser on startup.",
    )
    args = parser.parse_args()

    saved_cfg = _load_share_config(ROOT_DIR)
    requested_port = args.port if args.port is not None else (int(saved_cfg["port"]) if saved_cfg.get("port") else None)
    resolved_password = str(args.password or saved_cfg.get("password") or "")

    os.chdir(ROOT_DIR)
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = None
    port = requested_port or 8080
    candidates = [requested_port] if requested_port else list(range(8080, 8095))
    for p in candidates:
        try:
            httpd = socketserver.ThreadingTCPServer((args.host, p), MuseumHandler)
            port = p
            break
        except OSError:
            continue

    if httpd is None:
        raise RuntimeError(f"Could not bind port(s): {candidates}")

    httpd.museum_password = resolved_password  # type: ignore[attr-defined]
    httpd.museum_read_only = bool(args.read_only)  # type: ignore[attr-defined]
    httpd.museum_session_secret = secrets.token_bytes(32)  # type: ignore[attr-defined]

    display_host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    url = f"http://{display_host}:{port}/viewer/viewer.html"
    print("=" * 60)
    print("OFFLINE MUSEUM VIEWER SERVER")
    print("=" * 60)
    print(f"Archive Root:   {ROOT_DIR}")
    print(f"Bind Address:   http://{args.host}:{port}/viewer/viewer.html")
    print(f"Password Gate:  {'ENABLED' if httpd.museum_password else 'OFF (Local Only)'}")
    print(f"Share ReadOnly: {'ENABLED' if httpd.museum_read_only else 'Standard'}")
    print("Press Ctrl+C to stop the viewer server.")
    print("=" * 60)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nViewer server stopped safely.")


if __name__ == "__main__":
    main()
