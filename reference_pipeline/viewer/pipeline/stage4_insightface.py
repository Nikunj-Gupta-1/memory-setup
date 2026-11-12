import os
import sys
from pathlib import Path
_viewer_root = Path(__file__).resolve().parent.parent
if str(_viewer_root) not in sys.path:
    sys.path.insert(0, str(_viewer_root))

import time
import json
import sqlite3
import argparse
from datetime import datetime, timezone
import cv2
import numpy as np

from pipeline.config import WORKSPACE_ROOT, VIEWER_DIR, CONTENT_DIR, DB_PATH, REPORTS_DIR, MIN_FREE_DISK_GB

def check_disk_space() -> float:
    st = os.statvfs(WORKSPACE_ROOT)
    return (st.f_bavail * st.f_frsize) / (1024 ** 3)

def init_face_table(conn: sqlite3.Connection, truncate=False):
    cursor = conn.cursor()
    if truncate:
        print("Truncating people_faces table per user request for full fresh rescan...")
        cursor.execute("DROP TABLE IF EXISTS people_faces")
        
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS people_faces (
            face_id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id TEXT NOT NULL REFERENCES media_items(id),
            cluster_id INTEGER DEFAULT -1,
            person_name TEXT,
            bounding_box TEXT NOT NULL,
            confidence REAL NOT NULL,
            embedding BLOB NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_faces_media ON people_faces(media_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_faces_cluster ON people_faces(cluster_id)")
    conn.commit()

def run_insightface_scan(limit=None, truncate=False, batch_size=200):
    print("=" * 60)
    print("STAGE 4 UPGRADE: FULL ARCFACE / INSIGHTFACE BIOMETRIC SCAN")
    print("=" * 60)
    
    free_gb = check_disk_space()
    print(f"Free disk space: {free_gb:.2f} GiB (Safety threshold: {MIN_FREE_DISK_GB} GiB)")
    if free_gb < MIN_FREE_DISK_GB:
        raise RuntimeError("Insufficient disk space to proceed!")
        
    from insightface.app import FaceAnalysis
    
    print("\nInitializing InsightFace model pack (buffalo_l)...")
    t0 = time.time()
    app = FaceAnalysis(name='buffalo_l', providers=['CoreMLExecutionProvider', 'CPUExecutionProvider'])
    app.prepare(ctx_id=0, det_size=(640, 640))
    print(f"✓ Model initialized in {time.time() - t0:.2f}s (CoreML/Apple Silicon Neural Engine enabled)\n")
    
    conn = sqlite3.connect(DB_PATH)
    init_face_table(conn, truncate=truncate)
    cursor = conn.cursor()
    
    # Query all master photos with their processing and current paths
    cursor.execute("""
        SELECT m.id, m.current_path, p.processing_path
        FROM media_items m
        JOIN processing_inputs p ON m.id = p.media_id
        WHERE m.media_type = 'photo'
        ORDER BY m.id ASC
    """)
    all_photos = cursor.fetchall()
    
    # Determine already scanned photos if resuming
    cursor.execute("SELECT DISTINCT media_id FROM people_faces")
    already_scanned = set(r[0] for r in cursor.fetchall())
    
    if truncate:
        already_scanned.clear()
        
    pending = [r for r in all_photos if r[0] not in already_scanned]
    if limit:
        pending = pending[:limit]
        
    total_to_scan = len(pending)
    print(f"Total master photos: {len(all_photos):,}")
    print(f"Already scanned: {len(already_scanned):,}")
    print(f"Pending to scan: {total_to_scan:,}\n")
    
    if total_to_scan == 0:
        print("All photos already scanned! Proceeding to clustering...")
        conn.close()
        return
        
    t_start = time.time()
    faces_detected_total = 0
    photos_with_faces_count = 0
    records_to_insert = []
    
    checkpoint_file = REPORTS_DIR / 'stage4_insightface_checkpoint.json'
    
    for idx, (mid, cur_path, proc_path) in enumerate(pending):
        # Smart Hybrid Access: Use proxy for HEIC, master for JPG/PNG
        img_path = WORKSPACE_ROOT / proc_path
        if not img_path.exists():
            img_path = WORKSPACE_ROOT / cur_path
            
        img = cv2.imread(str(img_path))
        if img is None:
            continue
            
        h, w = img.shape[:2]
        faces = app.get(img)
        
        if faces:
            photos_with_faces_count += 1
            now_iso = datetime.now(timezone.utc).isoformat()
            
            for f in faces:
                if f.det_score < 0.45:
                    continue
                    
                emb = f.embedding
                norm = np.linalg.norm(emb)
                if norm > 0:
                    emb = emb / norm
                emb_bytes = emb.astype(np.float32).tobytes()
                
                # Normalize bbox to [x, y, w, h] in range 0..1
                bx1, by1, bx2, by2 = f.bbox
                norm_box = [
                    round(max(0.0, float(bx1) / w), 4),
                    round(max(0.0, float(by1) / h), 4),
                    round(min(1.0, float(bx2 - bx1) / w), 4),
                    round(min(1.0, float(by2 - by1) / h), 4)
                ]
                
                records_to_insert.append((
                    mid,
                    -1,
                    None,
                    json.dumps(norm_box),
                    float(f.det_score),
                    emb_bytes,
                    now_iso
                ))
                faces_detected_total += 1
                
        # Batch insert to SQLite
        if len(records_to_insert) >= batch_size:
            cursor.executemany("""
                INSERT INTO people_faces (
                    media_id, cluster_id, person_name, bounding_box, confidence, embedding, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, records_to_insert)
            conn.commit()
            records_to_insert = []
            
        # Logging & Progress
        if (idx + 1) % 100 == 0 or (idx + 1) == total_to_scan:
            elapsed = time.time() - t_start
            fps = (idx + 1) / max(0.001, elapsed)
            remaining_secs = (total_to_scan - (idx + 1)) / max(0.1, fps)
            eta_str = f"{int(remaining_secs // 60)}m {int(remaining_secs % 60)}s"
            
            print(f"[{idx+1:,}/{total_to_scan:,}] {fps:.1f} img/s | Faces: {faces_detected_total:,} | Photos w/ Faces: {photos_with_faces_count:,} | ETA: {eta_str}", flush=True)
            
            # Save checkpoint
            with open(checkpoint_file, 'w') as cf:
                json.dump({
                    'scanned': idx + 1,
                    'total': total_to_scan,
                    'faces_detected': faces_detected_total,
                    'updated_at': datetime.now(timezone.utc).isoformat()
                }, cf)
                
    # Final flush
    if records_to_insert:
        cursor.executemany("""
            INSERT INTO people_faces (
                media_id, cluster_id, person_name, bounding_box, confidence, embedding, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, records_to_insert)
        conn.commit()
        
    total_elapsed = time.time() - t_start
    print("\n" + "=" * 60)
    print("BIOMETRIC SCANNING COMPLETE")
    print("=" * 60)
    print(f"Scanned: {total_to_scan:,} photos in {total_elapsed/60:.1f} minutes ({total_to_scan/total_elapsed:.1f} img/s).")
    print(f"Total Faces Extracted: {faces_detected_total:,} faces across {photos_with_faces_count:,} photos.")
    
    conn.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=None, help='Limit number of photos to scan')
    parser.add_argument('--truncate', action='store_true', help='Drop and recreate people_faces table before scan')
    parser.add_argument('--batch-size', type=int, default=200, help='SQLite batch insert size')
    args = parser.parse_args()
    
    run_insightface_scan(limit=args.limit, truncate=args.truncate, batch_size=args.batch_size)
