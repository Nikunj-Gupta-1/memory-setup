from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS media_items (
    id TEXT PRIMARY KEY,
    media_type TEXT NOT NULL,
    original_rel_path TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    original_ext TEXT NOT NULL,
    current_path TEXT NOT NULL,
    current_filename TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    sha256_original TEXT,
    companion_group_id TEXT,
    companion_role TEXT DEFAULT 'none',
    has_unknown_date INTEGER DEFAULT 0,
    capture_timestamp TEXT,
    capture_source TEXT DEFAULT 'none',
    matched_dated_id TEXT,
    matched_timestamp TEXT,
    processing_status TEXT NOT NULL,
    face_scanned INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metadata_exif (
    media_id TEXT PRIMARY KEY REFERENCES media_items(id),
    make TEXT,
    model TEXT,
    lens_model TEXT,
    software TEXT,
    date_time_original TEXT,
    file_modify_date TEXT,
    gps_latitude REAL,
    gps_longitude REAL,
    gps_altitude REAL,
    city TEXT,
    state TEXT,
    country TEXT,
    orientation INTEGER,
    iso INTEGER,
    exposure_time TEXT,
    f_number REAL,
    focal_length REAL,
    raw_exif_json TEXT
);

CREATE TABLE IF NOT EXISTS processing_inputs (
    media_id TEXT PRIMARY KEY REFERENCES media_items(id),
    processing_path TEXT NOT NULL,
    is_reused_original INTEGER NOT NULL,
    format TEXT NOT NULL,
    pixel_width INTEGER NOT NULL,
    pixel_height INTEGER NOT NULL,
    aspect_ratio REAL NOT NULL,
    megapixels REAL NOT NULL,
    color_mode TEXT NOT NULL,
    sha256_processing TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    validation_error TEXT,
    dhash TEXT
);

CREATE TABLE IF NOT EXISTS ai_descriptions (
    media_id TEXT PRIMARY KEY REFERENCES media_items(id),
    model_name TEXT NOT NULL,
    short_caption TEXT NOT NULL,
    detailed_description TEXT NOT NULL,
    tags TEXT NOT NULL,
    scene_type TEXT,
    primary_subjects TEXT,
    detected_text TEXT,
    is_document_or_homework INTEGER DEFAULT 0,
    raw_response TEXT,
    described_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people_faces (
    face_id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id TEXT NOT NULL REFERENCES media_items(id),
    person_name TEXT,
    cluster_id INTEGER DEFAULT -1,
    bounding_box TEXT NOT NULL,
    confidence REAL NOT NULL,
    embedding BLOB NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id TEXT,
    stage TEXT NOT NULL,
    action TEXT NOT NULL,
    from_path TEXT,
    to_path TEXT,
    status TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    details TEXT
);

CREATE TABLE IF NOT EXISTS checkpoints (
    stage TEXT PRIMARY KEY,
    completed_at TEXT NOT NULL,
    total_items INTEGER NOT NULL,
    success_count INTEGER NOT NULL,
    failure_count INTEGER NOT NULL,
    report_file TEXT NOT NULL,
    summary_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_media_status ON media_items(processing_status);
CREATE INDEX IF NOT EXISTS idx_media_type ON media_items(media_type);
CREATE INDEX IF NOT EXISTS idx_media_companion ON media_items(companion_group_id);
CREATE INDEX IF NOT EXISTS idx_media_curpath ON media_items(current_path);
CREATE INDEX IF NOT EXISTS idx_media_sha256 ON media_items(sha256_original);
CREATE INDEX IF NOT EXISTS idx_op_media ON operation_history(media_id);
CREATE INDEX IF NOT EXISTS idx_faces_media ON people_faces(media_id);
CREATE INDEX IF NOT EXISTS idx_faces_cluster ON people_faces(cluster_id);

CREATE VIRTUAL TABLE IF NOT EXISTS ai_search_fts USING fts5(
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
);
"""


def get_connection(db_path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=60.0)
        conn.row_factory = sqlite3.Row
        return conn

    conn = sqlite3.connect(str(db_path), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _parse_iso_utc(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def backfill_legacy_face_scanned(conn: sqlite3.Connection, reports_dir: Path) -> int:
    """
    Mark previously scanned zero-face photos as `face_scanned = 1` when a completed
    legacy Stage 4 checkpoint (`stage4_insightface_checkpoint.json`) covers them.
    """
    ckpt_path = reports_dir / "stage4_insightface_checkpoint.json"
    if not ckpt_path.is_file():
        return 0
    try:
        ckpt = json.loads(ckpt_path.read_text(encoding="utf-8"))
        scanned = int(ckpt.get("scanned") or 0)
        total = int(ckpt.get("total") or 0)
        ckpt_dt = _parse_iso_utc(ckpt.get("updated_at"))
    except Exception:
        return 0

    if total <= 0 or scanned < total or ckpt_dt is None:
        return 0

    pending_rows = conn.execute(
        "SELECT id, created_at FROM media_items "
        "WHERE media_type = 'photo' AND COALESCE(face_scanned, 0) = 0"
    ).fetchall()
    if not pending_rows:
        return 0

    covered_ids = []
    for row in pending_rows:
        mid = row[0]
        created_dt = _parse_iso_utc(row[1])
        if created_dt is not None and created_dt <= ckpt_dt:
            covered_ids.append((mid,))

    if not covered_ids:
        return 0

    conn.executemany("UPDATE media_items SET face_scanned = 1 WHERE id = ?", covered_ids)
    return len(covered_ids)


def ensure_schema_migrations(conn: sqlite3.Connection) -> None:
    """Idempotently migrate older catalog.sqlite databases without data loss."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(media_items)")
    media_cols = {row[1] for row in cursor.fetchall()}
    if media_cols:
        if "face_scanned" not in media_cols:
            conn.execute("ALTER TABLE media_items ADD COLUMN face_scanned INTEGER DEFAULT 0")
            conn.execute(
                "UPDATE media_items SET face_scanned = 1 "
                "WHERE id IN (SELECT DISTINCT media_id FROM people_faces)"
            )
        if "matched_dated_id" not in media_cols:
            conn.execute("ALTER TABLE media_items ADD COLUMN matched_dated_id TEXT")
        if "matched_timestamp" not in media_cols:
            conn.execute("ALTER TABLE media_items ADD COLUMN matched_timestamp TEXT")

        # Consolidate any legacy Stage-3 `ai_manifest` match pointers into `media_items` and drop the redundant table.
        has_legacy_manifest = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ai_manifest'"
        ).fetchone()
        if has_legacy_manifest:
            manifest_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(ai_manifest)").fetchall()
            }
            if {"id", "matched_dated_id", "matched_timestamp"}.issubset(manifest_cols):
                conn.execute(
                    """
                    UPDATE media_items
                    SET matched_dated_id = COALESCE(
                            matched_dated_id,
                            (SELECT m.matched_dated_id FROM ai_manifest m WHERE m.id = media_items.id)
                        ),
                        matched_timestamp = COALESCE(
                            matched_timestamp,
                            (SELECT m.matched_timestamp FROM ai_manifest m WHERE m.id = media_items.id)
                        )
                    WHERE EXISTS (
                        SELECT 1 FROM ai_manifest m
                        WHERE m.id = media_items.id AND m.matched_dated_id IS NOT NULL
                    )
                    """
                )
            conn.execute("DROP TABLE IF EXISTS ai_manifest")

        conn.execute(
            """
            UPDATE media_items
            SET sha256_original = (
                SELECT p.sha256_processing
                FROM processing_inputs p
                WHERE p.media_id = media_items.id
                  AND p.is_reused_original = 1
                  AND COALESCE(p.sha256_processing, '') != ''
            )
            WHERE sha256_original IS NULL
              AND EXISTS (
                SELECT 1
                FROM processing_inputs p
                WHERE p.media_id = media_items.id
                  AND p.is_reused_original = 1
                  AND COALESCE(p.sha256_processing, '') != ''
              )
            """
        )

        db_rows = conn.execute("PRAGMA database_list").fetchall()
        for _, db_name, db_file in db_rows:
            if db_name == "main" and db_file:
                backfill_legacy_face_scanned(conn, Path(db_file).parent / "reports")
                break

    cursor.execute("PRAGMA table_info(processing_inputs)")
    cols = {row[1] for row in cursor.fetchall()}
    if cols and "dhash" not in cols:
        conn.execute("ALTER TABLE processing_inputs ADD COLUMN dhash TEXT")

    cursor.execute("PRAGMA table_info(people_faces)")
    face_cols = {row[1] for row in cursor.fetchall()}
    if face_cols and "created_at" not in face_cols:
        conn.execute("ALTER TABLE people_faces ADD COLUMN created_at TEXT")


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        ensure_schema_migrations(conn)
        conn.commit()


def log_operation(
    conn: sqlite3.Connection,
    media_id: Optional[str],
    stage: str,
    action: str,
    from_path: Optional[str],
    to_path: Optional[str],
    status: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO operation_history (media_id, stage, action, from_path, to_path, status, timestamp, details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            media_id,
            stage,
            action,
            from_path,
            to_path,
            status,
            now,
            json.dumps(details) if details else None,
        ),
    )


def record_checkpoint(
    conn: sqlite3.Connection,
    stage: str,
    total_items: int,
    success_count: int,
    failure_count: int,
    report_file: str,
    summary: Dict[str, Any],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO checkpoints (stage, completed_at, total_items, success_count, failure_count, report_file, summary_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stage,
            now,
            total_items,
            success_count,
            failure_count,
            report_file,
            json.dumps(summary, indent=2),
        ),
    )
