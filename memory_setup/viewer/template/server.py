#!/usr/bin/env python3
"""Zero-dependency local streaming server and live SQLite mutation API for Offline Museum Viewer."""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socketserver
import sqlite3
import webbrowser
from pathlib import Path


CUR_DIR = Path(__file__).resolve().parent
ROOT_DIR = CUR_DIR.parent if CUR_DIR.name == "viewer" else CUR_DIR
VIEWER_DIR = ROOT_DIR / "viewer"
DB_PATH = VIEWER_DIR / "catalog.sqlite"


def _refresh_catalog_data_js() -> None:
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
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
        if proc.startswith("viewer/"):
            p_web = proc[len("viewer/") :]
        elif proc.startswith("content/"):
            p_web = "../" + proc
        else:
            p_web = proc
        orig_web = ("../" + cur) if cur.startswith("content/") else cur
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
    conn.close()

    data_js_path = VIEWER_DIR / "catalog_data.js"
    with open(data_js_path, "w", encoding="utf-8") as fp:
        fp.write("// Auto-generated Catalog Data for Offline Museum Viewer\n")
        fp.write(f"const ALL_PHOTOS = {json.dumps(items)};\n\n")
        fp.write(f"const MAP_LOCATIONS = {json.dumps(locations)};\n\n")
        fp.write(f"const CORE_CLUSTERS = {json.dumps(core_clusters)};\n\n")
        fp.write(f"const EXTENDED_CLUSTERS = {json.dumps(extended_clusters)};\n")


class MuseumHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

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

        if self.path == "/api/people/rename":
            updates = body.get("updates", [])
            conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
            for item in updates:
                conn.execute(
                    "UPDATE people_faces SET person_name = ? WHERE cluster_id = ?",
                    (item["name"], int(item["cluster_id"])),
                )
            conn.commit()
            conn.close()
            _refresh_catalog_data_js()
            self._send_json(200, {"ok": True, "updated": len(updates)})
            return

        if self.path == "/api/people/merge":
            src_id = int(body["source_id"])
            tgt_id = int(body["target_id"])
            conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
            conn.execute(
                "UPDATE people_faces SET cluster_id = ? WHERE cluster_id = ?",
                (tgt_id, src_id),
            )
            conn.commit()
            conn.close()
            _refresh_catalog_data_js()
            self._send_json(200, {"ok": True, "source_id": src_id, "target_id": tgt_id})
            return

        if self.path == "/api/homework/quarantine":
            archive_dir = ROOT_DIR / "content" / "documents" / "homework_archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
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
                src = ROOT_DIR / cur_rel
                if src.exists():
                    dst = archive_dir / src.name
                    shutil.move(str(src), str(dst))
                    new_rel = dst.relative_to(ROOT_DIR).as_posix()
                    conn.execute(
                        "UPDATE media_items SET current_path = ? WHERE id = ?",
                        (new_rel, mid),
                    )
                    moved += 1
            conn.commit()
            conn.close()
            _refresh_catalog_data_js()
            self._send_json(200, {"ok": True, "moved_count": moved})
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
