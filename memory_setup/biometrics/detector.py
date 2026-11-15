from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from memory_setup.config import ArchiveContext
from memory_setup.db import backfill_legacy_face_scanned, get_connection, init_db, record_checkpoint
from memory_setup.setup.checker import detect_onnx_providers

ProgressCallback = Optional[Callable[[Dict[str, Any]], None]]


def run_biometric_face_scan(
    ctx: ArchiveContext,
    limit: Optional[int] = None,
    truncate: bool = False,
    batch_size: int = 200,
    det_score_thresh: float = 0.45,
    progress_cb: ProgressCallback = None,
    reprocess: Optional[bool] = None,
    stop_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    """
    Scan photos using InsightFace SCRFD-10GF + ArcFace ResNet-50 (buffalo_l).
    Stores 512-d L2-normalized embeddings and normalized top-left [x, y, w, h] boxes.
    """
    try:
        import cv2  # type: ignore
        from insightface.app import FaceAnalysis  # type: ignore
    except ImportError as exc:
        return {
            "status": "skipped",
            "reason": f"Biometric dependencies missing ({exc}). Install memory-setup[biometrics].",
            "scanned": 0,
            "faces_detected": 0,
        }

    init_db(ctx.db_path)
    conn = get_connection(ctx.db_path)
    cursor = conn.cursor()
    do_reprocess = bool(truncate or (ctx.reprocess if reprocess is None else reprocess))

    if truncate:
        cursor.execute("DELETE FROM people_faces")
        cursor.execute("UPDATE media_items SET face_scanned = 0")
        conn.commit()
    elif not do_reprocess:
        if backfill_legacy_face_scanned(conn, ctx.reports_dir):
            conn.commit()

    rows = cursor.execute(
        """
        SELECT m.id, m.current_path, p.processing_path, COALESCE(m.face_scanned, 0) AS face_scanned
        FROM media_items m
        JOIN processing_inputs p ON m.id = p.media_id
        WHERE m.media_type = 'photo'
        ORDER BY m.id ASC
        """
    ).fetchall()

    already_scanned = set(
        r[0] for r in cursor.execute("SELECT DISTINCT media_id FROM people_faces").fetchall()
    )
    if do_reprocess:
        pending = list(rows)
    else:
        pending = [r for r in rows if not r[3] and r[0] not in already_scanned]

    if limit is not None:
        pending = pending[:limit]

    if not pending:
        scanned_count = sum(1 for r in rows if r[3] or r[0] in already_scanned)
        summary = {
            "status": "complete",
            "scanned": 0,
            "faces_detected": 0,
            "already_scanned": scanned_count,
        }
        if limit is None:
            ctx.reports_dir.mkdir(parents=True, exist_ok=True)
            report_path = ctx.reports_dir / "stage4_faces_report.json"
            report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            record_checkpoint(conn, "stage4_faces", len(rows), scanned_count, 0, str(report_path), summary)
            conn.commit()
        conn.close()
        return summary

    providers = detect_onnx_providers() or ["CPUExecutionProvider"]
    app = FaceAnalysis(name="buffalo_l", providers=providers)
    app.prepare(ctx_id=0, det_size=(640, 640))

    t_start = time.time()
    faces_total = 0
    photos_with_faces = 0
    records_to_insert: List[tuple] = []
    scanned_ids_batch: List[str] = []

    def _flush_batch() -> None:
        nonlocal records_to_insert, scanned_ids_batch
        if do_reprocess and not truncate and scanned_ids_batch:
            cursor.executemany(
                "DELETE FROM people_faces WHERE media_id = ?",
                [(mid,) for mid in scanned_ids_batch],
            )
        if records_to_insert:
            cursor.executemany(
                """
                INSERT INTO people_faces (
                    media_id, person_name, cluster_id, bounding_box, confidence, embedding, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                records_to_insert,
            )
            records_to_insert.clear()
        if scanned_ids_batch:
            cursor.executemany(
                "UPDATE media_items SET face_scanned = 1 WHERE id = ?",
                [(mid,) for mid in scanned_ids_batch],
            )
            scanned_ids_batch.clear()
        conn.commit()

    interrupted = False
    scanned_count_run = 0
    for idx, (mid, cur_path, proc_path, _) in enumerate(pending, 1):
        if stop_event and stop_event.is_set():
            interrupted = True
            break

        img_path = ctx.resolve(proc_path)
        if not img_path.exists():
            img_path = ctx.resolve(cur_path)
        if not img_path.exists():
            continue

        img = cv2.imread(str(img_path))
        if img is None:
            continue

        h, w = img.shape[:2]
        faces = app.get(img)
        scanned_ids_batch.append(mid)
        scanned_count_run += 1
        if faces:
            now_iso = datetime.now(timezone.utc).isoformat()
            photo_had_face = False
            for f in faces:
                if float(f.det_score) < det_score_thresh:
                    continue
                emb = np.asarray(f.embedding, dtype=np.float32)
                norm = float(np.linalg.norm(emb))
                if norm > 0:
                    emb = emb / norm
                emb_bytes = emb.astype(np.float32).tobytes()

                bx1, by1, bx2, by2 = f.bbox
                norm_box = [
                    round(max(0.0, float(bx1) / w), 4),
                    round(max(0.0, float(by1) / h), 4),
                    round(min(1.0, float(bx2 - bx1) / w), 4),
                    round(min(1.0, float(by2 - by1) / h), 4),
                ]
                records_to_insert.append(
                    (mid, None, -2, json.dumps(norm_box), float(f.det_score), emb_bytes, now_iso)
                )
                faces_total += 1
                photo_had_face = True
            if photo_had_face:
                photos_with_faces += 1

        if len(records_to_insert) >= batch_size or len(scanned_ids_batch) >= batch_size:
            _flush_batch()

        if progress_cb and (idx % 50 == 0 or idx == len(pending)):
            progress_cb(
                {
                    "stage": "stage4_faces",
                    "current": idx,
                    "total": len(pending),
                    "faces_detected": faces_total,
                    "message": f"Scanned {idx:,}/{len(pending):,} photos ({faces_total:,} faces)",
                }
            )

    _flush_batch()

    elapsed = time.time() - t_start
    summary = {
        "status": "paused" if interrupted else "complete",
        "scanned": scanned_count_run,
        "faces_detected": faces_total,
        "photos_with_faces": photos_with_faces,
        "elapsed_seconds": round(elapsed, 2),
        "providers": providers,
    }
    if not interrupted and limit is None:
        ctx.reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = ctx.reports_dir / "stage4_faces_report.json"
        report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        record_checkpoint(conn, "stage4_faces", len(rows), len(rows), 0, str(report_path), summary)
        conn.commit()
    conn.close()
    return summary
