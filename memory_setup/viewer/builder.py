from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from memory_setup.config import ArchiveContext
from memory_setup.db import get_connection, init_db, log_operation, record_checkpoint

TEMPLATE_DIR = Path(__file__).resolve().parent / "template"


def build_fts5_index(conn: sqlite3.Connection) -> int:
    """Rebuild SQLite FTS5 virtual search table across captions, people names, locations, cameras, and timestamps."""
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS ai_search_fts")
    cursor.execute(
        """
        CREATE VIRTUAL TABLE ai_search_fts USING fts5(
            media_id UNINDEXED,
            media_type,
            filename,
            short_caption,
            detailed_description,
            tags,
            scene_type,
            person_names,
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
            media_id, media_type, filename, short_caption, detailed_description, tags,
            scene_type, person_names, camera_model, city, country, capture_timestamp
        )
        SELECT
            m.id,
            m.media_type,
            COALESCE(NULLIF(m.original_filename, ''), m.current_filename, ''),
            COALESCE(a.short_caption, ''),
            COALESCE(a.detailed_description, ''),
            COALESCE(a.tags, ''),
            COALESCE(a.scene_type, ''),
            COALESCE(pf.person_names, ''),
            COALESCE(e.model, ''),
            COALESCE(e.city, ''),
            COALESCE(e.country, ''),
            COALESCE(NULLIF(m.matched_timestamp, ''), m.capture_timestamp, '')
        FROM media_items m
        LEFT JOIN ai_descriptions a ON m.id = a.media_id
        LEFT JOIN metadata_exif e ON m.id = e.media_id
        LEFT JOIN (
            SELECT media_id, GROUP_CONCAT(DISTINCT person_name) AS person_names
            FROM people_faces
            WHERE cluster_id >= 0 AND person_name IS NOT NULL AND person_name != ''
            GROUP BY media_id
        ) pf ON m.id = pf.media_id
        WHERE m.media_type IN ('photo', 'video', 'document')
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
            m.matched_dated_id, m.matched_timestamp,
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
        tags_list = json.loads(r[23]) if r[23] else []
        subjects_list = json.loads(r[25]) if r[25] else []
        items.append(
            {
                "id": mid,
                "media_type": r[1],
                "path": r[2],
                "file_size": r[3],
                "timestamp": r[4],
                "matched_dated_id": r[7],
                "matched_timestamp": r[8],
                "has_unknown_date": bool(r[5]),
                "companion_group": r[6],
                "camera": f"{r[9] or ''} {r[10] or ''}".strip() or None,
                "gps": {"lat": r[11], "lon": r[12]} if r[11] is not None and r[12] is not None else None,
                "location": {"city": r[13], "state": r[14], "country": r[15]} if r[15] else None,
                "dimensions": {
                    "w": r[16],
                    "h": r[17],
                    "aspect": r[18],
                    "mp": r[19],
                    "format": r[20],
                },
                "ai": {
                    "caption": r[21] or "",
                    "description": r[22] or "",
                    "tags": tags_list,
                    "scene_type": r[24] or "general",
                    "primary_subjects": subjects_list,
                    "is_document_or_homework": bool(r[26]) if r[26] is not None else False,
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
                "src": ctx.to_viewer_web_path(cur_p),
                "d": ts[:10] if ts else "",
                "ext": ext_clean,
                "sz": int(sz or 0),
                "fn": orig_fn or Path(cur_p).name,
                "live": role == "live_photo_video",
            }
        )

    ctx.catalog_data_js_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ctx.catalog_data_js_path, "w", encoding="utf-8") as fp:
        fp.write("// Auto-generated Catalog Data for Offline Museum Viewer\n")
        fp.write(f"const ALL_PHOTOS = {json.dumps(items)};\n\n")
        fp.write(f"const ALL_VIDEOS = {json.dumps(videos)};\n\n")
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
    record_checkpoint(conn, "stage5_museum", fts_count, fts_count, 0, ctx.to_rel(report_path), summary)
    conn.commit()
    conn.close()
    return summary


def list_person_clusters(ctx: ArchiveContext, limit: int = 100) -> List[Dict[str, Any]]:
    """Return face clusters ordered by photo/face frequency for Control Center management."""
    if not ctx.db_path.exists():
        return []
    conn = get_connection(ctx.db_path, readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT cluster_id, COUNT(*), MAX(person_name)
            FROM people_faces
            WHERE cluster_id >= 0
            GROUP BY cluster_id
            ORDER BY COUNT(*) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    clusters: List[Dict[str, Any]] = []
    for idx, (raw_cid, raw_cnt, raw_name) in enumerate(rows):
        cid = int(raw_cid)
        custom_name = (str(raw_name).strip() if raw_name else "")
        default_name = f"Family Member {cid + 1}" if idx < 9 else f"Recurring Person {cid + 1}"
        clusters.append(
            {
                "cluster_id": cid,
                "count": int(raw_cnt),
                "person_name": custom_name,
                "default_name": default_name,
                "display_name": custom_name or default_name,
                "has_avatar": (ctx.avatars_dir / f"cluster_{cid}.jpg").exists(),
            }
        )
    return clusters


def rename_person_clusters(ctx: ArchiveContext, updates: List[Dict[str, Any]]) -> int:
    """Persist custom cluster names directly to SQLite, sync FTS5, and re-export catalog_data.js."""
    init_db(ctx.db_path)
    conn = get_connection(ctx.db_path)
    for item in updates:
        cid = int(item["cluster_id"])
        clean_name = str(item.get("name") or "").strip() or None
        conn.execute(
            "UPDATE people_faces SET person_name = ? WHERE cluster_id = ?",
            (clean_name, cid),
        )
        log_operation(
            conn,
            None,
            "control_center_people",
            "RENAME_PERSON",
            None,
            None,
            "SUCCESS",
            {"cluster_id": cid, "person_name": clean_name},
        )
    conn.commit()
    build_fts5_index(conn)
    export_catalog_data_js(ctx, conn)
    conn.close()
    return len(updates)


def merge_person_clusters(ctx: ArchiveContext, source_id: int, target_id: int) -> None:
    """Merge source_id cluster into target_id cluster in SQLite, sync FTS5, and re-export catalog_data.js."""
    src_id = int(source_id)
    tgt_id = int(target_id)
    if src_id == tgt_id:
        raise ValueError("Source and target cluster IDs must differ.")

    init_db(ctx.db_path)
    conn = get_connection(ctx.db_path)
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
    log_operation(
        conn,
        None,
        "control_center_people",
        "MERGE_PERSON",
        str(src_id),
        str(tgt_id),
        "SUCCESS",
        {"source_id": src_id, "target_id": tgt_id, "target_name": target_name},
    )
    conn.commit()
    build_fts5_index(conn)
    export_catalog_data_js(ctx, conn)
    conn.close()


def quarantine_homework_files(ctx: ArchiveContext) -> int:
    """Move flagged homework/study documents into `content/documents/homework_archive/`."""
    archive_dir = ctx.documents_dir / "homework_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    init_db(ctx.db_path)
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
    archive_resolved = archive_dir.resolve()
    now_iso = datetime.now(timezone.utc).isoformat()
    for mid, cur_rel in rows:
        src = ctx.resolve(cur_rel)
        if src.exists() and src.parent.resolve() != archive_resolved:
            dst = archive_dir / src.name
            if dst.exists():
                dst = archive_dir / f"{mid}_{src.name}"
            shutil.move(str(src), str(dst))
            new_rel = dst.relative_to(ctx.archive_root).as_posix()
            conn.execute(
                "UPDATE media_items SET current_path = ?, current_filename = ?, updated_at = ? WHERE id = ?",
                (new_rel, dst.name, now_iso, mid),
            )
            conn.execute(
                "UPDATE processing_inputs SET processing_path = ? WHERE media_id = ? AND is_reused_original = 1",
                (new_rel, mid),
            )
            log_operation(
                conn,
                mid,
                "control_center_quarantine",
                "QUARANTINE",
                cur_rel,
                new_rel,
                "SUCCESS",
            )
            moved += 1
    conn.commit()
    export_catalog_data_js(ctx, conn)
    conn.close()
    return moved
