from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from memory_setup.config import ArchiveContext
from memory_setup.db import get_connection, init_db, record_checkpoint

TEMPLATE_DIR = Path(__file__).resolve().parent / "template"


def build_fts5_index(conn: sqlite3.Connection) -> int:
    """Rebuild SQLite FTS5 virtual search table across captions, locations, cameras, and timestamps."""
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS ai_search_fts")
    cursor.execute(
        """
        CREATE VIRTUAL TABLE ai_search_fts USING fts5(
            media_id UNINDEXED,
            short_caption,
            detailed_description,
            tags,
            scene_type,
            camera_model,
            city,
            country,
            capture_timestamp
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO ai_search_fts (
            media_id, short_caption, detailed_description, tags, scene_type,
            camera_model, city, country, capture_timestamp
        )
        SELECT
            m.id,
            COALESCE(a.short_caption, ''),
            COALESCE(a.detailed_description, ''),
            COALESCE(a.tags, ''),
            COALESCE(a.scene_type, ''),
            COALESCE(e.model, ''),
            COALESCE(e.city, ''),
            COALESCE(e.country, ''),
            COALESCE(m.capture_timestamp, '')
        FROM media_items m
        LEFT JOIN ai_descriptions a ON m.id = a.media_id
        LEFT JOIN metadata_exif e ON m.id = e.media_id
        WHERE m.media_type = 'photo'
        """
    )
    conn.commit()
    return int(cursor.execute("SELECT COUNT(*) FROM ai_search_fts").fetchone()[0])


def export_catalog_json(ctx: ArchiveContext, conn: sqlite3.Connection) -> Path:
    """Export full structured JSON catalog to `viewer/catalog.json`."""
    cursor = conn.cursor()
    rows = cursor.execute(
        """
        SELECT
            m.id, m.media_type, m.current_path, m.file_size_bytes,
            m.capture_timestamp, m.has_unknown_date, m.companion_group_id,
            e.make, e.model, e.gps_latitude, e.gps_longitude, e.city, e.state, e.country,
            p.pixel_width, p.pixel_height, p.aspect_ratio, p.megapixels, p.format,
            a.short_caption, a.detailed_description, a.tags, a.scene_type,
            a.primary_subjects, a.is_document_or_homework
        FROM media_items m
        LEFT JOIN metadata_exif e ON m.id = e.media_id
        LEFT JOIN processing_inputs p ON m.id = p.media_id
        LEFT JOIN ai_descriptions a ON m.id = a.media_id
        ORDER BY m.capture_timestamp ASC, m.id ASC
        """
    ).fetchall()

    face_map: Dict[str, List[Dict[str, Any]]] = {}
    for mid, cid, name, conf in cursor.execute(
        "SELECT media_id, cluster_id, person_name, confidence FROM people_faces"
    ).fetchall():
        face_map.setdefault(mid, []).append(
            {"cluster_id": cid, "person_name": name, "confidence": conf}
        )

    items = []
    for r in rows:
        mid = r[0]
        tags_list = json.loads(r[21]) if r[21] else []
        subjects_list = json.loads(r[23]) if r[23] else []
        items.append(
            {
                "id": mid,
                "media_type": r[1],
                "path": r[2],
                "file_size": r[3],
                "timestamp": r[4],
                "has_unknown_date": bool(r[5]),
                "companion_group": r[6],
                "camera": f"{r[7] or ''} {r[8] or ''}".strip() or None,
                "gps": {"lat": r[9], "lon": r[10]} if r[9] is not None and r[10] is not None else None,
                "location": {"city": r[11], "state": r[12], "country": r[13]} if r[13] else None,
                "dimensions": {
                    "w": r[14],
                    "h": r[15],
                    "aspect": r[16],
                    "mp": r[17],
                    "format": r[18],
                },
                "ai": {
                    "caption": r[19] or "",
                    "description": r[20] or "",
                    "tags": tags_list,
                    "scene_type": r[22] or "general",
                    "primary_subjects": subjects_list,
                    "is_document_or_homework": bool(r[24]) if r[24] is not None else False,
                },
                "faces": face_map.get(mid, []),
            }
        )

    ctx.json_catalog_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.json_catalog_path.write_text(json.dumps(items, indent=2), encoding="utf-8")
    return ctx.json_catalog_path


def export_catalog_data_js(ctx: ArchiveContext, conn: sqlite3.Connection) -> Path:
    """Export compact browser payload `viewer/catalog_data.js`, preserving custom person names."""
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

    face_clusters: Dict[str, List[int]] = {}
    for mid, cid in cursor.execute(
        "SELECT DISTINCT media_id, cluster_id FROM people_faces WHERE cluster_id >= 0"
    ).fetchall():
        face_clusters.setdefault(mid, []).append(int(cid))

    items = []
    for r in rows:
        mid = r[0]
        p_web = ctx.to_viewer_web_path(r[1])
        orig_web = ctx.to_viewer_web_path(r[2])
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
        {"id": int(r[0]), "count": int(r[1]), "name": r[2] or f"Family Member {int(r[0]) + 1}"}
        for r in clust_rows[:9]
    ]
    extended_clusters = [
        {"id": int(r[0]), "count": int(r[1]), "name": r[2] or f"Recurring Person {int(r[0]) + 1}"}
        for r in clust_rows[9:30]
    ]

    ctx.catalog_data_js_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ctx.catalog_data_js_path, "w", encoding="utf-8") as fp:
        fp.write("// Auto-generated Catalog Data for Offline Museum Viewer\n")
        fp.write(f"const ALL_PHOTOS = {json.dumps(items)};\n\n")
        fp.write(f"const MAP_LOCATIONS = {json.dumps(locations)};\n\n")
        fp.write(f"const CORE_CLUSTERS = {json.dumps(core_clusters)};\n\n")
        fp.write(f"const EXTENDED_CLUSTERS = {json.dumps(extended_clusters)};\n")

    return ctx.catalog_data_js_path


def deploy_viewer_bundle(ctx: ArchiveContext, overwrite_html: bool = False) -> Dict[str, Any]:
    """
    Deploy immutable viewer.html (anti-clobber protected), server.py, and 1-click launchers.
    """
    ctx.ensure_layout()
    html_written = False

    if overwrite_html or not ctx.viewer_html_path.exists():
        shutil.copy2(TEMPLATE_DIR / "viewer.html", ctx.viewer_html_path)
        html_written = True

    server_dst = ctx.viewer_dir / "server.py"
    shutil.copy2(TEMPLATE_DIR / "server.py", server_dst)

    cmd_dst = ctx.viewer_dir / "start_viewer.command"
    shutil.copy2(TEMPLATE_DIR / "start_viewer.command", cmd_dst)
    os.chmod(cmd_dst, 0o755)

    bat_dst = ctx.viewer_dir / "start_viewer.bat"
    shutil.copy2(TEMPLATE_DIR / "start_viewer.bat", bat_dst)

    # Top-level launcher shortcuts in archive_root
    top_cmd = ctx.archive_root / "start_viewer.command"
    shutil.copy2(TEMPLATE_DIR / "start_viewer.command", top_cmd)
    os.chmod(top_cmd, 0o755)

    top_bat = ctx.archive_root / "start_viewer.bat"
    shutil.copy2(TEMPLATE_DIR / "start_viewer.bat", top_bat)

    # Relative symlink viewer/content -> ../content
    symlink_path = ctx.viewer_dir / "content"
    if not symlink_path.exists() and not symlink_path.is_symlink():
        try:
            symlink_path.symlink_to("../content")
        except OSError:
            pass

    return {
        "viewer_html_written": html_written,
        "viewer_html_path": str(ctx.viewer_html_path),
        "server_path": str(server_dst),
    }


def run_stage5_museum(ctx: ArchiveContext, overwrite_html: bool = False) -> Dict[str, Any]:
    """Execute Stage 5: FTS5 index, JSON & JS exports, and anti-clobber viewer bundle deployment."""
    init_db(ctx.db_path)
    conn = get_connection(ctx.db_path)

    fts_count = build_fts5_index(conn)
    export_catalog_json(ctx, conn)
    export_catalog_data_js(ctx, conn)
    deploy_info = deploy_viewer_bundle(ctx, overwrite_html=overwrite_html)

    summary = {
        "stage": "stage5_museum_synthesis",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "fts_indexed_count": fts_count,
        "viewer_html_written": deploy_info["viewer_html_written"],
        "catalog_data_js": str(ctx.catalog_data_js_path),
        "catalog_json": str(ctx.json_catalog_path),
    }
    report_path = ctx.reports_dir / "stage5_report.json"
    ctx.reports_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    record_checkpoint(conn, "stage5_museum", fts_count, fts_count, 0, str(report_path), summary)
    conn.commit()
    conn.close()
    return summary


def rename_person_clusters(ctx: ArchiveContext, updates: List[Dict[str, Any]]) -> int:
    """Persist custom cluster names directly to SQLite and re-export catalog_data.js."""
    conn = get_connection(ctx.db_path)
    for item in updates:
        conn.execute(
            "UPDATE people_faces SET person_name = ? WHERE cluster_id = ?",
            (str(item["name"]).strip(), int(item["cluster_id"])),
        )
    conn.commit()
    export_catalog_data_js(ctx, conn)
    conn.close()
    return len(updates)


def merge_person_clusters(ctx: ArchiveContext, source_id: int, target_id: int) -> None:
    """Merge source_id cluster into target_id cluster in SQLite and re-export catalog_data.js."""
    conn = get_connection(ctx.db_path)
    conn.execute(
        "UPDATE people_faces SET cluster_id = ? WHERE cluster_id = ?",
        (int(target_id), int(source_id)),
    )
    conn.commit()
    export_catalog_data_js(ctx, conn)
    conn.close()


def quarantine_homework_files(ctx: ArchiveContext) -> int:
    """Move flagged homework/study documents into `content/documents/homework_archive/`."""
    archive_dir = ctx.documents_dir / "homework_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    conn = get_connection(ctx.db_path)
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
        src = ctx.resolve(cur_rel)
        if src.exists() and src.parent != archive_dir:
            dst = archive_dir / src.name
            shutil.move(str(src), str(dst))
            new_rel = dst.relative_to(ctx.archive_root).as_posix()
            conn.execute("UPDATE media_items SET current_path = ? WHERE id = ?", (new_rel, mid))
            moved += 1
    conn.commit()
    export_catalog_data_js(ctx, conn)
    conn.close()
    return moved
