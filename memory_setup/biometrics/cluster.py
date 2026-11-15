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


def _generate_cluster_avatars(
    ctx: ArchiveContext,
    cursor: Any,
    cluster_ids: List[int],
    dim: int,
    overwrite: bool = False,
) -> int:
    """Crop 160x160 face thumbnails into `viewer/avatars/cluster_<id>.jpg`."""
    ctx.avatars_dir.mkdir(parents=True, exist_ok=True)
    avatars_generated = 0
    for cid in cluster_ids:
        avatar_path = ctx.avatars_dir / f"cluster_{cid}.jpg"
        if avatar_path.exists() and not overwrite:
            continue

        row = cursor.execute(
            """
            SELECT p.media_id, p.bounding_box, m.processing_path
            FROM people_faces p
            JOIN processing_inputs m ON p.media_id = m.media_id
            WHERE p.cluster_id = ?
            ORDER BY p.confidence DESC
            LIMIT 1
            """,
            (cid,),
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
                    crop.convert("RGB").save(avatar_path, "JPEG", quality=85)
                    avatars_generated += 1
        except Exception:
            continue
    return avatars_generated


def cluster_and_generate_avatars(
    ctx: ArchiveContext,
    thresh: Optional[float] = None,
    min_cluster_size: int = 3,
    top_avatars: int = 30,
    recluster: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Cluster embeddings in `people_faces`, update SQLite, and crop 160x160 avatars.

    By default (`recluster=False`), existing `cluster_id >= 0` clusters and manual
    `person_name` labels are preserved: unclustered faces (`cluster_id = -2`) are matched
    against existing cluster centroids first, and only unmatched faces are clustered into
    new IDs above `MAX(cluster_id)`.
    """
    do_recluster = ctx.recluster if recluster is None else recluster
    conn = get_connection(ctx.db_path)
    cursor = conn.cursor()

    existing_rows = cursor.execute(
        """
        SELECT face_id, cluster_id, person_name, embedding
        FROM people_faces
        WHERE embedding IS NOT NULL AND cluster_id >= 0
        """
    ).fetchall()

    t0 = time.time()
    batch_size = 2000

    if not do_recluster and existing_rows:
        pending_rows = cursor.execute(
            "SELECT face_id, embedding FROM people_faces WHERE embedding IS NOT NULL AND cluster_id = -2"
        ).fetchall()

        sample_emb = np.frombuffer(existing_rows[0][3], dtype=np.float32)
        dim = int(sample_emb.shape[0])

        if not pending_rows:
            top_cids = [
                int(r[0])
                for r in cursor.execute(
                    """
                    SELECT cluster_id
                    FROM people_faces
                    WHERE cluster_id >= 0
                    GROUP BY cluster_id
                    ORDER BY COUNT(*) DESC
                    LIMIT ?
                    """,
                    (top_avatars,),
                ).fetchall()
            ]
            avatars_generated = _generate_cluster_avatars(ctx, cursor, top_cids, dim=dim, overwrite=False)
            total_vectors = int(
                cursor.execute("SELECT COUNT(*) FROM people_faces WHERE embedding IS NOT NULL").fetchone()[0] or 0
            )
            recurring_clusters = int(
                cursor.execute("SELECT COUNT(DISTINCT cluster_id) FROM people_faces WHERE cluster_id >= 0").fetchone()[0]
                or 0
            )
            conn.close()
            return {
                "status": "complete",
                "total_vectors": total_vectors,
                "new_faces_clustered": 0,
                "recurring_clusters": recurring_clusters,
                "preserved_existing": True,
                "avatars_generated": avatars_generated,
                "elapsed_seconds": round(time.time() - t0, 2),
            }

        centroid_sums: Dict[int, np.ndarray] = {}
        cluster_counts: Dict[int, int] = {}
        cluster_names: Dict[int, Optional[str]] = {}
        for _, cid, pname, emb_blob in existing_rows:
            cid_int = int(cid)
            vec = np.frombuffer(emb_blob, dtype=np.float32).copy()
            vnorm = float(np.linalg.norm(vec))
            if vnorm > 0:
                vec /= vnorm
            if cid_int not in centroid_sums:
                centroid_sums[cid_int] = vec
                cluster_counts[cid_int] = 1
                cluster_names[cid_int] = pname
            else:
                centroid_sums[cid_int] += vec
                cluster_counts[cid_int] += 1
                if pname and not cluster_names.get(cid_int):
                    cluster_names[cid_int] = pname

        centroid_cids = sorted(centroid_sums.keys())
        centroids_list: List[np.ndarray] = []
        for cid_int in centroid_cids:
            cvec = centroid_sums[cid_int]
            cnorm = float(np.linalg.norm(cvec))
            if cnorm > 0:
                cvec = cvec / cnorm
            centroids_list.append(cvec)
        centroid_matrix = np.vstack(centroids_list).astype(np.float32)

        eff_thresh = thresh if thresh is not None else (0.48 if dim == 512 else 0.82)
        assignments: List[Tuple[int, Optional[str], int]] = []
        unmatched_ids: List[int] = []
        unmatched_vecs: List[np.ndarray] = []

        for fid, emb_blob in pending_rows:
            vec = np.frombuffer(emb_blob, dtype=np.float32).copy()
            vnorm = float(np.linalg.norm(vec))
            if vnorm > 0:
                vec /= vnorm
            sims = np.dot(centroid_matrix, vec)
            best_idx = int(np.argmax(sims))
            if sims[best_idx] >= eff_thresh:
                matched_cid = centroid_cids[best_idx]
                cnt = cluster_counts[matched_cid]
                updated = centroid_matrix[best_idx] * cnt + vec
                unorm = float(np.linalg.norm(updated))
                if unorm > 0:
                    updated /= unorm
                centroid_matrix[best_idx] = updated
                cluster_counts[matched_cid] = cnt + 1
                assignments.append((matched_cid, cluster_names.get(matched_cid), fid))
            else:
                unmatched_ids.append(fid)
                unmatched_vecs.append(vec)

        new_clusters_created = 0
        if unmatched_vecs:
            unmatched_X = np.vstack(unmatched_vecs)
            new_labels, new_stats = cluster_embeddings(
                unmatched_X, thresh=eff_thresh, min_cluster_size=min_cluster_size
            )
            max_existing_cid = max(centroid_cids)
            new_clusters_created = int(new_stats["recurring_clusters"])
            for fid, lbl in zip(unmatched_ids, new_labels):
                lbl_int = int(lbl)
                final_cid = (max_existing_cid + 1 + lbl_int) if lbl_int >= 0 else -1
                assignments.append((final_cid, None, fid))

        for i in range(0, len(assignments), batch_size):
            cursor.executemany(
                "UPDATE people_faces SET cluster_id = ?, person_name = COALESCE(person_name, ?) WHERE face_id = ?",
                assignments[i : i + batch_size],
            )
        conn.commit()

        top_cids = [
            int(r[0])
            for r in cursor.execute(
                """
                SELECT cluster_id
                FROM people_faces
                WHERE cluster_id >= 0
                GROUP BY cluster_id
                ORDER BY COUNT(*) DESC
                LIMIT ?
                """,
                (top_avatars,),
            ).fetchall()
        ]
        avatars_generated = _generate_cluster_avatars(ctx, cursor, top_cids, dim=dim, overwrite=False)
        total_vectors = int(
            cursor.execute("SELECT COUNT(*) FROM people_faces WHERE embedding IS NOT NULL").fetchone()[0] or 0
        )
        recurring_clusters = int(
            cursor.execute("SELECT COUNT(DISTINCT cluster_id) FROM people_faces WHERE cluster_id >= 0").fetchone()[0]
            or 0
        )
        conn.close()
        return {
            "status": "complete",
            "total_vectors": total_vectors,
            "dim": dim,
            "threshold": eff_thresh,
            "new_faces_clustered": len(assignments),
            "new_clusters_created": new_clusters_created,
            "recurring_clusters": recurring_clusters,
            "preserved_existing": True,
            "avatars_generated": avatars_generated,
            "elapsed_seconds": round(time.time() - t0, 2),
        }

    rows = cursor.execute(
        "SELECT face_id, embedding FROM people_faces WHERE embedding IS NOT NULL"
    ).fetchall()
    if not rows:
        conn.close()
        return {"status": "empty", "total_vectors": 0, "recurring_clusters": 0}

    face_ids = [r[0] for r in rows]
    embs = [np.frombuffer(r[1], dtype=np.float32) for r in rows]
    X = np.vstack(embs)

    ranked_labels, stats = cluster_embeddings(X, thresh=thresh, min_cluster_size=min_cluster_size)
    dim = stats["dim"]

    update_tuples = [(int(ranked_labels[i]), face_ids[i]) for i in range(len(face_ids))]
    for i in range(0, len(update_tuples), batch_size):
        cursor.executemany(
            "UPDATE people_faces SET cluster_id = ? WHERE face_id = ?",
            update_tuples[i : i + batch_size],
        )
    conn.commit()

    target_cids = list(range(min(top_avatars, stats["recurring_clusters"])))
    avatars_generated = _generate_cluster_avatars(
        ctx, cursor, target_cids, dim=dim, overwrite=do_recluster
    )

    conn.close()
    stats["avatars_generated"] = avatars_generated
    stats["preserved_existing"] = False
    stats["elapsed_seconds"] = round(time.time() - t0, 2)
    return stats
