from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from memory_setup.config import ArchiveContext
from memory_setup.db import get_connection


def cluster_embeddings(
    embeddings: np.ndarray,
    thresh: Optional[float] = None,
    min_cluster_size: int = 3,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Vectorized BLAS Leader Centroid Clustering with dynamic centroid buffer capacity.
    Returns (ranked_labels, stats) where Top 0..8 are Core Family, >=9 Extended, and -1 is Noise.
    """
    if embeddings.size == 0:
        return np.array([], dtype=np.int32), {"num_raw_clusters": 0, "recurring_clusters": 0, "noise_count": 0}

    X = np.asarray(embeddings, dtype=np.float32)
    if X.ndim == 1:
        X = X.reshape(1, -1)

    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    X_norm = X / norms

    n_items, dim = X_norm.shape
    if thresh is None:
        thresh = 0.48 if dim == 512 else 0.82

    # Dynamic centroid matrix capacity: never overflows regardless of archive face count
    capacity = max(1024, n_items)
    centroids = np.zeros((capacity, dim), dtype=np.float32)
    cluster_counts = np.zeros(capacity, dtype=np.int32)
    labels = np.empty(n_items, dtype=np.int32)
    num_c = 0

    for idx in range(n_items):
        vec = X_norm[idx]
        if num_c == 0:
            centroids[0] = vec
            cluster_counts[0] = 1
            labels[idx] = 0
            num_c = 1
            continue

        sims = np.dot(centroids[:num_c], vec)
        best_idx = int(np.argmax(sims))
        if sims[best_idx] >= thresh:
            labels[idx] = best_idx
            cnt = cluster_counts[best_idx]
            new_vec = centroids[best_idx] * cnt + vec
            norm_val = float(np.linalg.norm(new_vec))
            if norm_val > 0:
                new_vec /= norm_val
            centroids[best_idx] = new_vec
            cluster_counts[best_idx] = cnt + 1
        else:
            if num_c >= capacity:
                new_cap = capacity * 2
                new_centroids = np.zeros((new_cap, dim), dtype=np.float32)
                new_counts = np.zeros(new_cap, dtype=np.int32)
                new_centroids[:capacity] = centroids
                new_counts[:capacity] = cluster_counts
                centroids = new_centroids
                cluster_counts = new_counts
                capacity = new_cap

            labels[idx] = num_c
            centroids[num_c] = vec
            cluster_counts[num_c] = 1
            num_c += 1

    counts = Counter(labels.tolist())
    sorted_valid = [cid for cid, cnt in counts.most_common() if cnt >= min_cluster_size]
    noise_count = sum(cnt for cid, cnt in counts.items() if cnt < min_cluster_size)

    cluster_map = {old_cid: rank for rank, old_cid in enumerate(sorted_valid)}
    ranked_labels = np.array([cluster_map.get(int(lbl), -1) for lbl in labels], dtype=np.int32)

    stats = {
        "total_vectors": n_items,
        "dim": dim,
        "threshold": thresh,
        "num_raw_clusters": num_c,
        "recurring_clusters": len(sorted_valid),
        "noise_count": noise_count,
    }
    return ranked_labels, stats


def cluster_and_generate_avatars(
    ctx: ArchiveContext,
    thresh: Optional[float] = None,
    min_cluster_size: int = 3,
    top_avatars: int = 30,
) -> Dict[str, Any]:
    """Cluster all embeddings in `people_faces`, update SQLite, and crop 160x160 avatars."""
    conn = get_connection(ctx.db_path)
    cursor = conn.cursor()

    rows = cursor.execute(
        "SELECT face_id, embedding FROM people_faces WHERE embedding IS NOT NULL"
    ).fetchall()
    if not rows:
        conn.close()
        return {"status": "empty", "total_vectors": 0, "recurring_clusters": 0}

    face_ids = [r[0] for r in rows]
    embs = [np.frombuffer(r[1], dtype=np.float32) for r in rows]
    X = np.vstack(embs)

    t0 = time.time()
    ranked_labels, stats = cluster_embeddings(X, thresh=thresh, min_cluster_size=min_cluster_size)
    dim = stats["dim"]

    update_tuples = [(int(ranked_labels[i]), face_ids[i]) for i in range(len(face_ids))]
    batch_size = 2000
    for i in range(0, len(update_tuples), batch_size):
        cursor.executemany(
            "UPDATE people_faces SET cluster_id = ? WHERE face_id = ?",
            update_tuples[i : i + batch_size],
        )
    conn.commit()

    ctx.avatars_dir.mkdir(parents=True, exist_ok=True)
    avatars_generated = 0
    for rank in range(min(top_avatars, stats["recurring_clusters"])):
        row = cursor.execute(
            """
            SELECT p.media_id, p.bounding_box, m.processing_path
            FROM people_faces p
            JOIN processing_inputs m ON p.media_id = m.media_id
            WHERE p.cluster_id = ?
            ORDER BY p.confidence DESC
            LIMIT 1
            """,
            (rank,),
        ).fetchone()
        if not row:
            continue

        _, box_str, proc_path = row
        try:
            box = json.loads(box_str)
        except Exception:
            continue

        full_img_path = ctx.resolve(proc_path)
        if not full_img_path.exists():
            continue

        try:
            with Image.open(full_img_path) as img:
                img_w, img_h = img.size
                bx, by, bw, bh = box
                px = max(0, int((bx - bw * 0.15) * img_w))
                if dim == 512:
                    py = max(0, int((by - bh * 0.15) * img_h))
                else:
                    py = max(0, int((1.0 - by - bh - bh * 0.15) * img_h))
                pw = min(img_w - px, int(bw * 1.3 * img_w))
                ph = min(img_h - py, int(bh * 1.3 * img_h))
                if pw > 10 and ph > 10:
                    crop = img.crop((px, py, px + pw, py + ph))
                    crop.thumbnail((160, 160))
                    crop.convert("RGB").save(ctx.avatars_dir / f"cluster_{rank}.jpg", "JPEG", quality=85)
                    avatars_generated += 1
        except Exception:
            continue

    conn.close()
    stats["avatars_generated"] = avatars_generated
    stats["elapsed_seconds"] = round(time.time() - t0, 2)
    return stats
