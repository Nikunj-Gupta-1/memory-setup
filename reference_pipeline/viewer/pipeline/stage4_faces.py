import os
import sys
from pathlib import Path
_viewer_root = Path(__file__).resolve().parent.parent
if str(_viewer_root) not in sys.path:
    sys.path.insert(0, str(_viewer_root))

import json
import time
import base64
import sqlite3
import subprocess
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
import numpy as np

from pipeline.config import (
    WORKSPACE_ROOT,
    VIEWER_DIR,
    DB_PATH,
    REPORTS_DIR,
    AVATARS_DIR,
    MANIFEST_JSON_PATH,
)
from pipeline.db import get_connection

def dbscan_cosine(embeddings: np.ndarray, eps: float = 0.38, min_samples: int = 4) -> np.ndarray:
    """
    Density-Based Spatial Clustering of Applications with Noise (DBSCAN)
    using Cosine Distance metric.
    Unassigned points (strangers / one-off faces) are labeled -1 (Noise).
    """
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normed = embeddings / norms
    
    n = len(normed)
    labels = np.full(n, -1, dtype=int)
    cluster_id = 0
    visited = np.zeros(n, dtype=bool)
    sim_threshold = 1.0 - eps
    
    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        
        sims = np.dot(normed, normed[i])
        neighbors = np.where(sims >= sim_threshold)[0]
        
        if len(neighbors) < min_samples:
            labels[i] = -1
        else:
            labels[i] = cluster_id
            queue = list(neighbors)
            while queue:
                curr = queue.pop(0)
                if not visited[curr]:
                    visited[curr] = True
                    curr_sims = np.dot(normed, normed[curr])
                    curr_neighbors = np.where(curr_sims >= sim_threshold)[0]
                    if len(curr_neighbors) >= min_samples:
                        queue.extend([k for k in curr_neighbors if not visited[k]])
                if labels[curr] == -1:
                    labels[curr] = cluster_id
            cluster_id += 1
            
    return labels

def run_worker_chunk(worker_id: int, items_chunk: List[Dict[str, Any]], bin_extractor: Path, db_path: Path):
    if not items_chunk:
        return 0, 0
        
    proc = subprocess.Popen(
        [str(bin_extractor)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    
    def feed():
        for it in items_chunk:
            proc.stdin.write(f"{it['id']}\t{it['processing_path']}\n")
        proc.stdin.close()
        
    import threading
    t = threading.Thread(target=feed)
    t.start()
    
    conn = sqlite3.connect(str(db_path), timeout=60.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    cursor = conn.cursor()
    
    batch = []
    batch_size = 50
    now_iso = datetime.now(timezone.utc).isoformat()
    faces_count = 0
    img_count = 0
    
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            res = json.loads(line)
            mid = res['media_id']
            faces = res.get('faces', [])
            img_count += 1
            
            for idx, face in enumerate(faces, 1):
                fid = f"{mid}_F{idx}"
                b64 = face['embedding_b64']
                raw_bytes = base64.b64decode(b64)
                box_str = json.dumps(face['box'])
                conf = face['confidence']
                batch.append((fid, mid, None, -1, box_str, raw_bytes, conf, now_iso))
                faces_count += 1
                
            if len(batch) >= batch_size:
                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO people_faces (
                        face_id, media_id, person_name, cluster_id, bounding_box, embedding, confidence, detected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    batch
                )
                conn.commit()
                batch.clear()
        except Exception as e:
            pass
            
    if batch:
        cursor.executemany(
            """
            INSERT OR REPLACE INTO people_faces (
                face_id, media_id, person_name, cluster_id, bounding_box, embedding, confidence, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch
        )
        conn.commit()
        batch.clear()
        
    t.join()
    proc.wait()
    conn.close()
    return img_count, faces_count

def extract_and_cluster_faces():
    print("=" * 60)
    print("STAGE 4: FACE RECOGNITION & OPEN-ENDED CLUSTERING")
    print("=" * 60)
    
    bin_extractor = VIEWER_DIR / 'bin_face_extractor'
    if not bin_extractor.exists():
        bin_extractor = WORKSPACE_ROOT / 'bin_face_extractor'
    if not bin_extractor.exists():
        raise RuntimeError("bin_face_extractor binary not found! Please compile pipeline/face_extractor.swift.")
        
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Load manifest items
    manifest_path = MANIFEST_JSON_PATH if MANIFEST_JSON_PATH.exists() else (WORKSPACE_ROOT / 'ai_manifest.json')
    with open(manifest_path) as f:
        manifest = json.load(f)
        
    print(f"Loaded {len(manifest):,} images from ai_manifest.json.")
    
    # Check already extracted faces
    cursor.execute("SELECT DISTINCT media_id FROM people_faces")
    already_done = set(r[0] for r in cursor.fetchall())
    print(f"Already scanned for faces: {len(already_done):,} images.")
    
    remaining = [item for item in manifest if item['id'] not in already_done]
    print(f"Remaining images to scan for faces: {len(remaining):,}")
    
    if remaining:
        num_workers = 4
        print(f"Distributing remaining {len(remaining):,} images across {num_workers} parallel Apple Silicon workers...")
        chunks = [remaining[i::num_workers] for i in range(num_workers)]
        
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=num_workers) as ex:
            futures = [
                ex.submit(run_worker_chunk, wid, chunks[wid], bin_extractor, DB_PATH)
                for wid in range(num_workers)
            ]
            results = [f.result() for f in futures]
            
        total_scanned = sum(r[0] for r in results)
        total_faces = sum(r[1] for r in results)
        elapsed = time.time() - t0
        rate = total_scanned / elapsed if elapsed > 0 else 0
        print(f"\nFace scanning complete in {elapsed:.1f}s ({rate:.1f} img/s). Scanned: {total_scanned:,} images, Found: {total_faces:,} faces.")
        
    # 2. Run Open-Ended DBSCAN Clustering on All Embeddings
    print("\nRunning Open-Ended DBSCAN Density Clustering on all face embeddings...")
    cursor.execute("SELECT face_id, embedding FROM people_faces WHERE embedding IS NOT NULL")
    face_rows = cursor.fetchall()
    print(f"Total face records for clustering: {len(face_rows):,}")
    
    if len(face_rows) < 5:
        print("Not enough faces detected to cluster.")
        conn.close()
        return
        
    face_ids = [r[0] for r in face_rows]
    embeddings_list = []
    for r in face_rows:
        blob = r[1]
        vec = np.frombuffer(blob, dtype=np.float32)
        embeddings_list.append(vec)
        
    X = np.vstack(embeddings_list)
    print(f"Constructed feature matrix: {X.shape}")
    
    # DBSCAN with cosine distance eps=0.38 (strict similarity to avoid false positives)
    raw_labels = dbscan_cosine(X, eps=0.38, min_samples=4)
    
    # Count cluster frequencies
    from collections import Counter
    cluster_counts = Counter(raw_labels)
    noise_count = cluster_counts.get(-1, 0)
    print(f"DBSCAN clustering produced {len(cluster_counts)-1} distinct identity clusters (+ {noise_count:,} noise/stranger faces).")
    
    # Sort valid clusters by frequency descending
    sorted_clusters = [c for c, count in cluster_counts.most_common() if c != -1]
    
    # Map old cluster IDs to ranked IDs:
    # Top 9 -> 0..8 (Core Family Members)
    # Remaining -> 9..K (Extended Family & Friends)
    # Noise -> -1
    cluster_map = {-1: -1}
    for new_rank, old_cid in enumerate(sorted_clusters):
        cluster_map[old_cid] = new_rank
        
    ranked_labels = [cluster_map[l] for l in raw_labels]
    
    print("\nTop 15 Clusters by Face Count:")
    for rank in range(min(15, len(sorted_clusters))):
        old_cid = sorted_clusters[rank]
        count = cluster_counts[old_cid]
        tier = "Core Family" if rank < 9 else "Extended/Friends"
        print(f"  Cluster {rank:2d} ({tier}): {count:,} faces")
    print(f"  Noise / Strangers: {noise_count:,} faces")
    
    # 3. Update cluster_ids in database
    print("\nUpdating people_faces table with ranked cluster IDs...")
    update_tuples = [(ranked_labels[i], face_ids[i]) for i in range(len(face_ids))]
    batch_size = 500
    for i in range(0, len(update_tuples), batch_size):
        chunk = update_tuples[i:i + batch_size]
        cursor.executemany("UPDATE people_faces SET cluster_id = ? WHERE face_id = ?", chunk)
    conn.commit()
    print("✓ Updated all cluster IDs in SQLite!")
    
    # 4. Generate Avatars Directory for Viewer
    avatars_dir = AVATARS_DIR
    avatars_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nGenerating avatar thumbnails for top clusters in {avatars_dir}...")
    
    # For each of the top clusters (up to 30), pick the highest-confidence face
    for rank in range(min(30, len(sorted_clusters))):
        cursor.execute(
            """
            SELECT p.media_id, p.bounding_box, m.processing_path
            FROM people_faces p
            JOIN ai_manifest m ON p.media_id = m.id
            WHERE p.cluster_id = ?
            ORDER BY p.confidence DESC
            LIMIT 1
            """,
            (rank,)
        )
        row = cursor.fetchone()
        if row:
            mid, box_str, proc_path = row
            box = json.loads(box_str) # [x, y, w, h] normalized
            full_img_path = WORKSPACE_ROOT / proc_path
            if full_img_path.exists():
                try:
                    with Image.open(full_img_path) as img:
                        img_w, img_h = img.size
                        bx, by, bw, bh = box
                        # Vision box has origin at bottom-left
                        px = max(0, int((bx - bw * 0.15) * img_w))
                        py = max(0, int((1.0 - by - bh - bh * 0.15) * img_h))
                        pw = min(img_w - px, int(bw * 1.3 * img_w))
                        ph = min(img_h - py, int(bh * 1.3 * img_h))
                        if pw > 10 and ph > 10:
                            crop = img.crop((px, py, px + pw, py + ph))
                            crop.thumbnail((160, 160))
                            crop.convert('RGB').save(avatars_dir / f"cluster_{rank}.jpg", "JPEG", quality=85)
                except Exception as e:
                    print(f"Error generating avatar for cluster {rank}: {e}")
                    
    conn.close()
    print("✓ Face recognition & open-ended clustering complete!")

if __name__ == '__main__':
    extract_and_cluster_faces()
