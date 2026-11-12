import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from pipeline.config import DB_PATH

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
    processing_status TEXT NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_op_media ON operation_history(media_id);
CREATE INDEX IF NOT EXISTS idx_faces_media ON people_faces(media_id);
CREATE INDEX IF NOT EXISTS idx_faces_cluster ON people_faces(cluster_id);
"""

def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        
        # Migrations
        try:
            conn.execute("ALTER TABLE processing_inputs ADD COLUMN dhash TEXT")
        except sqlite3.OperationalError:
            pass # Already exists
            
        conn.commit()

def log_operation(conn: sqlite3.Connection, media_id: Optional[str], stage: str, action: str,
                  from_path: Optional[str], to_path: Optional[str], status: str, details: Optional[Dict[str, Any]] = None):
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
            json.dumps(details) if details else None
        )
    )

def record_checkpoint(conn: sqlite3.Connection, stage: str, total_items: int,
                      success_count: int, failure_count: int, report_file: str, summary: Dict[str, Any]):
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
            json.dumps(summary, indent=2)
        )
    )
