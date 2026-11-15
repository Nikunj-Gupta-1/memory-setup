#!/usr/bin/env python3
"""Zero-dependency local streaming server and live SQLite mutation API for Offline Museum Viewer."""

from __future__ import annotations

import http.server
import json
import mimetypes
import os
import platform
import shutil
import socketserver
import sqlite3
import subprocess
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Optional, Tuple


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
    target_db = db_path or (viewer_dir / "catalog.sqlite")
    if not target_db.exists():
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


class MuseumHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _get_roots(self) -> Tuple[Path, Path]:
        root = Path(getattr(self.server, "root_dir", ROOT_DIR)).resolve()
        db_path = Path(getattr(self.server, "db_path", root / "viewer" / "catalog.sqlite"))
        return root, db_path

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
        if target.is_dir():
            idx = target / "index.html"
            if idx.is_file():
                return idx
        return target

    def do_HEAD(self) -> None:
        target = self._resolve_static_target()
        if target is None:
            self.send_error(403, "Forbidden")
            return
        if target.is_dir():
            super().do_HEAD()
            return
        self._serve_file(target, head_only=True)

    def do_GET(self) -> None:
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

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(400, {"error": "Invalid JSON"})
            return

        root_dir, db_path = self._get_roots()

        if self.path == "/api/people/rename":
            updates = body.get("updates", [])
            conn = sqlite3.connect(str(db_path), timeout=60.0)
            for item in updates:
                conn.execute(
                    "UPDATE people_faces SET person_name = ? WHERE cluster_id = ?",
                    (item["name"], int(item["cluster_id"])),
                )
            conn.commit()
            conn.close()
            _refresh_catalog_data_js(root_dir=root_dir, db_path=db_path)
            self._send_json(200, {"ok": True, "updated": len(updates)})
            return

        if self.path == "/api/people/merge":
            src_id = int(body["source_id"])
            tgt_id = int(body["target_id"])
            conn = sqlite3.connect(str(db_path), timeout=60.0)
            conn.execute(
                "UPDATE people_faces SET cluster_id = ? WHERE cluster_id = ?",
                (tgt_id, src_id),
            )
            conn.commit()
            conn.close()
            _refresh_catalog_data_js(root_dir=root_dir, db_path=db_path)
            self._send_json(200, {"ok": True, "source_id": src_id, "target_id": tgt_id})
            return

        if self.path == "/api/homework/quarantine":
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
            for mid, cur_rel in rows:
                src = root_dir / cur_rel
                if src.exists() and src.parent.resolve() != archive_resolved:
                    dst = archive_dir / src.name
                    if dst.exists():
                        dst = archive_dir / f"{mid}_{src.name}"
                    shutil.move(str(src), str(dst))
                    new_rel = dst.relative_to(root_dir).as_posix()
                    conn.execute(
                        "UPDATE media_items SET current_path = ? WHERE id = ?",
                        (new_rel, mid),
                    )
                    conn.execute(
                        "UPDATE processing_inputs SET processing_path = ? WHERE media_id = ? AND is_reused_original = 1",
                        (new_rel, mid),
                    )
                    moved += 1
            conn.commit()
            conn.close()
            _refresh_catalog_data_js(root_dir=root_dir, db_path=db_path)
            self._send_json(200, {"ok": True, "moved_count": moved})
            return

        if self.path == "/api/open-media":
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
    os.chdir(ROOT_DIR)
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = None
    port = 8080
    for p in range(8080, 8095):
        try:
            httpd = socketserver.ThreadingTCPServer(("127.0.0.1", p), MuseumHandler)
            port = p
            break
        except OSError:
            continue

    if httpd is None:
        raise RuntimeError("Could not bind any port in range 8080-8094.")

    url = f"http://localhost:{port}/viewer/viewer.html"
    print("=" * 60)
    print("OFFLINE MUSEUM VIEWER LOCAL SERVER")
    print("=" * 60)
    print(f"Archive Root: {ROOT_DIR}")
    print(f"Opening Browser at: {url}")
    print("Press Ctrl+C to stop the viewer server.")
    print("=" * 60)
    webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nViewer server stopped safely.")


if __name__ == "__main__":
    main()
