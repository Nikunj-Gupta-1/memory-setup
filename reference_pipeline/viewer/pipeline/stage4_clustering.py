import os
import sys
from pathlib import Path
_viewer_root = Path(__file__).resolve().parent.parent
if str(_viewer_root) not in sys.path:
    sys.path.insert(0, str(_viewer_root))

import json
import time
import sqlite3
from collections import Counter
from PIL import Image
import numpy as np

from pipeline.config import WORKSPACE_ROOT, DB_PATH, AVATARS_DIR
from pipeline.db import get_connection

def cluster_and_save(thresh=None, min_cluster_size=3):
    print("=" * 60)
    print("STAGE 4: OPEN-ENDED FACE CLUSTERING & AVATAR GENERATION")
    print("=" * 60)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    print("Loading face embeddings from people_faces...")
    cursor.execute("SELECT face_id, embedding FROM people_faces WHERE embedding IS NOT NULL")
    rows = cursor.fetchall()
    if not rows:
        print("No face records found in people_faces!")
        conn.close()
        return
        
    face_ids = [r[0] for r in rows]
    embs = [np.frombuffer(r[1], dtype=np.float32) for r in rows]
    
    X = np.vstack(embs)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    X_norm = X / norms
    
    N = len(X_norm)
    dim = X_norm.shape[1]
    
    if thresh is None:
        thresh = 0.48 if dim == 512 else 0.82
        
    print(f"Loaded {N:,} face vectors ({dim}-dimensional). Running BLAS Leader Clustering (thresh={thresh:.2f})...")
    t0 = time.time()
    
    max_c = max(15000, N)
    centroids = np.zeros((max_c, dim), dtype=np.float32)
    cluster_counts = np.zeros(max_c, dtype=np.int32)
    labels = np.empty(N, dtype=np.int32)
    num_c = 0
    
    for idx in range(N):
        vec = X_norm[idx]
        if num_c == 0:
            centroids[0] = vec
            cluster_counts[0] = 1
            labels[idx] = 0
            num_c = 1
            continue
            
        sims = np.dot(centroids[:num_c], vec)
        best_idx = np.argmax(sims)
        best_sim = sims[best_idx]
        
        if best_sim >= thresh:
            labels[idx] = best_idx
            cnt = cluster_counts[best_idx]
            new_vec = centroids[best_idx] * cnt + vec
            new_vec /= np.linalg.norm(new_vec)
            centroids[best_idx] = new_vec
            cluster_counts[best_idx] += 1
        else:
            labels[idx] = num_c
            centroids[num_c] = vec
            cluster_counts[num_c] = 1
            num_c += 1
            
    t1 = time.time()
    print(f"Clustering complete in {t1-t0:.2f} seconds! Discovered {num_c:,} raw clusters.")
    
    # 2. Ranking & Noise Rejection
    counts = Counter(labels)
    min_cluster_size = 3
    
    # Sort clusters with count >= min_cluster_size descending
    sorted_valid = [cid for cid, cnt in counts.most_common() if cnt >= min_cluster_size]
    noise_count = sum(cnt for cid, cnt in counts.items() if cnt < min_cluster_size)
    
    print(f"Recurring identity clusters (>= 3 faces): {len(sorted_valid):,}")
    print(f"Isolated singletons & tourists labeled as Noise: {noise_count:,} faces")
    
    # Map raw cluster IDs to ranked IDs
    # Top 9 -> 0..8 (Core Family Members)
    # Remaining -> 9..K (Extended Family & Friends)
    # Noise -> -1
    cluster_map = {}
    for new_rank, old_cid in enumerate(sorted_valid):
        cluster_map[old_cid] = new_rank
        
    ranked_labels = [cluster_map.get(l, -1) for l in labels]
    
    print("\n" + "-" * 50)
    print("TOP 9 CORE FAMILY MEMBERS RANKING:")
    print("-" * 50)
    for rank in range(min(9, len(sorted_valid))):
        old_cid = sorted_valid[rank]
        cnt = counts[old_cid]
        print(f"  👑 Family Member {rank+1} (Cluster ID {rank}): {cnt:,} photos")
        
    print("\nSecondary Recurring Clusters (Extended Family & Friends preview):")
    for rank in range(9, min(16, len(sorted_valid))):
        old_cid = sorted_valid[rank]
        cnt = counts[old_cid]
        print(f"  🤝 Recurring Person {rank+1} (Cluster ID {rank}): {cnt:,} photos")
    print("-" * 50)
    
    # 3. Update SQLite database
    print("\nUpdating people_faces in catalog.sqlite with ranked cluster IDs...")
    update_tuples = [(ranked_labels[i], face_ids[i]) for i in range(N)]
    batch_size = 2000
    for i in range(0, len(update_tuples), batch_size):
        chunk = update_tuples[i:i + batch_size]
        cursor.executemany("UPDATE people_faces SET cluster_id = ? WHERE face_id = ?", chunk)
    conn.commit()
    print(f"✓ Successfully updated all {N:,} records in people_faces!")
    
    # 4. Generate Avatar Thumbnails
    avatars_dir = AVATARS_DIR
    avatars_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nGenerating avatar thumbnails for top clusters in {avatars_dir}...")
    
    # Pick the highest-confidence face for top 30 clusters
    for rank in range(min(30, len(sorted_valid))):
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
                        if dim == 512:
                            # InsightFace top-left origin
                            px = max(0, int((bx - bw * 0.15) * img_w))
                            py = max(0, int((by - bh * 0.15) * img_h))
                        else:
                            # Apple Vision bottom-left origin
                            px = max(0, int((bx - bw * 0.15) * img_w))
                            py = max(0, int((1.0 - by - bh - bh * 0.15) * img_h))
                        pw = min(img_w - px, int(bw * 1.3 * img_w))
                        ph = min(img_h - py, int(bh * 1.3 * img_h))
                        if pw > 10 and ph > 10:
                            crop = img.crop((px, py, px + pw, py + ph))
                            crop.thumbnail((160, 160))
                            crop.convert('RGB').save(avatars_dir / f"cluster_{rank}.jpg", "JPEG", quality=85)
                except Exception as e:
                    print(f"Warning: could not generate avatar for cluster {rank}: {e}")
                    
    conn.close()
    print(f"✓ Generated {len(list(avatars_dir.glob('*.jpg')))} avatar images in {avatars_dir}!")
    print("\nStage 4 Face Recognition & Clustering 100% COMPLETE!")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--thresh', type=float, default=None, help='Cosine similarity threshold')
    parser.add_argument('--min-size', type=int, default=3, help='Minimum cluster size')
    args = parser.parse_args()
    cluster_and_save(thresh=args.thresh, min_cluster_size=args.min_size)
