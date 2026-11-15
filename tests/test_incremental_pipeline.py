from __future__ import annotations

import json
import socketserver
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

from memory_setup.biometrics.cluster import cluster_and_generate_avatars
from memory_setup.biometrics.detector import run_biometric_face_scan
from memory_setup.config import ArchiveContext
from memory_setup.db import backfill_legacy_face_scanned, ensure_schema_migrations, get_connection, init_db
from memory_setup.ingestion.router import (
    _next_id_counters,
    run_stage1_consolidation,
    run_stage2_standardization,
    run_stage3_validation,
)
from memory_setup.viewer.builder import (
    quarantine_homework_files,
    rename_person_clusters,
    run_stage5_museum,
)
from memory_setup.viewer.template import server as standalone_server


def _make_jpg(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (240, 180), color=color)
    img.save(path, format="JPEG", quality=92)


def _unit_vec(seed: int, dim: int = 512) -> bytes:
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(dim).astype(np.float32)
    vec /= np.linalg.norm(vec)
    return vec.tobytes()


def _perturbed_vec(base_bytes: bytes, noise_scale: float = 0.02, seed: int = 99) -> bytes:
    rng = np.random.default_rng(seed)
    base = np.frombuffer(base_bytes, dtype=np.float32).copy()
    noisy = base + rng.standard_normal(base.shape[0]).astype(np.float32) * noise_scale
    noisy /= np.linalg.norm(noisy)
    return noisy.tobytes()


def test_id_counters_numeric_order_past_one_million(tmp_path: Path) -> None:
    db_path = tmp_path / "catalog.sqlite"
    init_db(db_path)
    conn = get_connection(db_path)
    now = "2026-01-01T00:00:00Z"
    for pid in ("P_000001", "P_999999", "P_1000000"):
        conn.execute(
            """
            INSERT INTO media_items (
                id, media_type, original_rel_path, original_filename, original_ext,
                current_path, current_filename, file_size_bytes, processing_status,
                created_at, updated_at
            ) VALUES (?, 'photo', 'a.jpg', 'a.jpg', '.jpg', 'content/a.jpg', 'a.jpg', 100, 'stage1_consolidated', ?, ?)
            """,
            (pid, now, now),
        )
    conn.commit()
    counters = _next_id_counters(conn)
    conn.close()
    assert counters["P"] == 1000001


def test_face_scanned_migration_backfill(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE media_items (
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
            companion_role TEXT,
            has_unknown_date INTEGER DEFAULT 0,
            capture_timestamp TEXT,
            capture_source TEXT,
            processing_status TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE people_faces (
            face_id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id TEXT NOT NULL,
            person_name TEXT,
            cluster_id INTEGER,
            bounding_box TEXT,
            confidence REAL,
            embedding BLOB,
            created_at TEXT
        );
        INSERT INTO media_items (id, media_type, original_rel_path, original_filename, original_ext, current_path, current_filename, file_size_bytes)
        VALUES ('P_000001', 'photo', 'a.jpg', 'a.jpg', '.jpg', 'content/a.jpg', 'a.jpg', 10),
               ('P_000002', 'photo', 'b.jpg', 'b.jpg', '.jpg', 'content/b.jpg', 'b.jpg', 10);
        INSERT INTO people_faces (media_id, cluster_id) VALUES ('P_000001', 0);
        """
    )
    conn.commit()
    conn.close()

    init_db(db_path)
    conn2 = get_connection(db_path, readonly=True)
    cols = {r[1] for r in conn2.execute("PRAGMA table_info(media_items)").fetchall()}
    assert "face_scanned" in cols
    scanned = dict(conn2.execute("SELECT id, face_scanned FROM media_items").fetchall())
    conn2.close()
    assert scanned["P_000001"] == 1
    assert scanned["P_000002"] == 0


def test_incremental_rerun_preserves_ai_faces_and_skips_runtime_files(tmp_path: Path) -> None:
    arch = tmp_path / "ArchiveInc"
    _make_jpg(arch / "Batch1" / "photo1.jpg", (210, 50, 50))
    _make_jpg(arch / "Batch1" / "photo2.jpg", (50, 190, 60))
    (arch / "Batch1" / "clip1.mov").write_bytes(b"\x00\x00\x00\x18ftypqt  clip1data")

    ctx = ArchiveContext(archive_root=arch, mode="in_place")
    res1 = run_stage1_consolidation(ctx)
    assert res1["success_count"] == 3
    run_stage2_standardization(ctx, max_workers=2)
    run_stage3_validation(ctx, max_workers=2)

    # Simulate pre-existing archive with NULL sha256_original, existing AI descriptions,
    # and user-curated named/merged face clusters + sentinel avatar
    alice_vec = _unit_vec(1)
    bob_vec = _unit_vec(2)
    conn = get_connection(ctx.db_path)
    conn.execute("UPDATE media_items SET sha256_original = NULL")
    conn.execute(
        """
        INSERT INTO ai_descriptions (
            media_id, model_name, short_caption, detailed_description, tags,
            scene_type, is_document_or_homework, described_at
        ) VALUES
            ('P_000001', 'moondream', 'Existing Beach', 'Existing Beach caption', '["beach"]', 'outdoor', 0, '2026-01-01'),
            ('P_000002', 'moondream', 'Existing Park', 'Existing Park caption', '["park"]', 'outdoor', 0, '2026-01-01')
        """
    )
    for seed in range(4):
        conn.execute(
            "INSERT INTO people_faces (media_id, person_name, cluster_id, bounding_box, confidence, embedding, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("P_000001", "Alice", 0, json.dumps([0.1, 0.1, 0.4, 0.4]), 0.99, _perturbed_vec(alice_vec, 0.01, seed + 10), "2026-01-01"),
        )
        conn.execute(
            "INSERT INTO people_faces (media_id, person_name, cluster_id, bounding_box, confidence, embedding, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("P_000002", None, 1, json.dumps([0.2, 0.2, 0.4, 0.4]), 0.98, _perturbed_vec(bob_vec, 0.01, seed + 20), "2026-01-01"),
        )
    conn.commit()
    conn.close()

    ctx.avatars_dir.mkdir(parents=True, exist_ok=True)
    sentinel_avatar = ctx.avatars_dir / "cluster_0.jpg"
    sentinel_avatar.write_bytes(b"SENTINEL_AVATAR_BYTES")

    # Drop archive-root runtime files that must never be ingested
    (arch / "start_viewer.command").write_text("#!/bin/bash\npython3 viewer/server.py\n", encoding="utf-8")
    (arch / "start_viewer.bat").write_text("@echo off\n", encoding="utf-8")
    (arch / "cleanup_homework.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (arch / "AGENT.md").write_text("# Docs\n", encoding="utf-8")

    # Drop a new trip folder with 1 exact duplicate of photo1.jpg + 1 new photo + 1 new mp4 video
    ro_conn = get_connection(ctx.db_path, readonly=True)
    dup_rel = ro_conn.execute("SELECT current_path FROM media_items WHERE id = 'P_000001'").fetchone()[0]
    ro_conn.close()
    dup_bytes = ctx.resolve(dup_rel).read_bytes()
    (arch / "NewTrip").mkdir(parents=True, exist_ok=True)
    (arch / "NewTrip" / "dup_of_photo1.jpg").write_bytes(dup_bytes)
    _make_jpg(arch / "NewTrip" / "new_mountain.jpg", (70, 90, 220))
    (arch / "NewTrip" / "new_clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypisomnewmp4")

    # Incremental re-run
    inc1 = run_stage1_consolidation(ctx)
    assert inc1["duplicates_skipped"] == 1
    assert inc1["success_count"] == 2  # only new_mountain.jpg + new_clip.mp4

    inc2 = run_stage2_standardization(ctx, max_workers=2)
    assert inc2["total_photos"] == 3
    assert inc2["metadata_extracted"] == 1
    assert inc2["newly_processed_inputs"] == 1
    assert inc2["processing_inputs_ready"] == 3

    inc3 = run_stage3_validation(ctx, max_workers=2)
    assert inc3["valid_images"] == 3
    assert inc3["corrupt_images"] == 0

    conn = get_connection(ctx.db_path, readonly=True)
    all_ids = [r[0] for r in conn.execute("SELECT id FROM media_items ORDER BY id ASC").fetchall()]
    assert all_ids == ["P_000001", "P_000002", "P_000003", "V_000001", "V_000002"]

    captions = dict(conn.execute("SELECT media_id, short_caption FROM ai_descriptions").fetchall())
    conn.close()
    assert captions == {"P_000001": "Existing Beach", "P_000002": "Existing Park"}

    # Simulate new face detections (-2 unclustered): 1 matching Alice + 3 forming brand-new Charlie
    charlie_vec = _unit_vec(77)
    conn_w = get_connection(ctx.db_path)
    conn_w.execute(
        "INSERT INTO people_faces (media_id, person_name, cluster_id, bounding_box, confidence, embedding, created_at) VALUES (?, NULL, -2, ?, 0.99, ?, '2026-02-01')",
        ("P_000003", json.dumps([0.15, 0.15, 0.4, 0.4]), _perturbed_vec(alice_vec, 0.01, 501)),
    )
    for seed in range(3):
        conn_w.execute(
            "INSERT INTO people_faces (media_id, person_name, cluster_id, bounding_box, confidence, embedding, created_at) VALUES (?, NULL, -2, ?, 0.97, ?, '2026-02-01')",
            ("P_000003", json.dumps([0.3, 0.2, 0.35, 0.35]), _perturbed_vec(charlie_vec, 0.01, seed + 600)),
        )
    conn_w.commit()
    conn_w.close()

    cstats = cluster_and_generate_avatars(ctx)
    assert cstats["preserved_existing"] is True
    assert cstats["new_faces_clustered"] == 4
    assert cstats["new_clusters_created"] == 1
    assert cstats["recurring_clusters"] == 3

    conn_check = get_connection(ctx.db_path, readonly=True)
    new_faces = [
        (r[0], r[1])
        for r in conn_check.execute(
            "SELECT cluster_id, person_name FROM people_faces WHERE media_id = 'P_000003' ORDER BY face_id ASC"
        ).fetchall()
    ]
    conn_check.close()

    assert new_faces[0] == (0, "Alice")
    assert all(cid >= 2 for cid, _ in new_faces[1:])
    assert len({cid for cid, _ in new_faces[1:]}) == 1
    # Sentinel avatar for cluster 0 must stay untouched; cluster 2 avatar generated
    assert sentinel_avatar.read_bytes() == b"SENTINEL_AVATAR_BYTES"
    new_cid = new_faces[1][0]
    assert (ctx.avatars_dir / f"cluster_{new_cid}.jpg").exists()


def test_legacy_stage4_checkpoint_backfills_zero_face_photos(tmp_path: Path) -> None:
    arch = tmp_path / "LegacyArchive"
    _make_jpg(arch / "Batch1" / "has_face.jpg", (200, 120, 80))
    _make_jpg(arch / "Batch1" / "landscape_no_face.jpg", (50, 140, 210))
    _make_jpg(arch / "Batch2" / "future_new.jpg", (90, 200, 90))

    ctx = ArchiveContext(archive_root=arch, mode="in_place")
    run_stage1_consolidation(ctx)
    run_stage2_standardization(ctx, max_workers=2)
    run_stage3_validation(ctx, max_workers=2)

    conn = get_connection(ctx.db_path)
    # Simulate two photos ingested before legacy Stage 4 completion and one new photo added later
    conn.execute(
        "UPDATE media_items SET created_at = '2026-11-12T01:09:06+00:00', face_scanned = 0 "
        "WHERE original_filename IN ('has_face.jpg', 'landscape_no_face.jpg')"
    )
    conn.execute(
        "UPDATE media_items SET created_at = '2026-11-15T12:00:00+00:00', face_scanned = 0 "
        "WHERE original_filename = 'future_new.jpg'"
    )
    ids = dict(conn.execute("SELECT original_filename, id FROM media_items").fetchall())
    conn.execute(
        "INSERT INTO people_faces (media_id, person_name, cluster_id, bounding_box, confidence, embedding, created_at) "
        "VALUES (?, NULL, 0, '[0.1,0.1,0.3,0.3]', 0.99, ?, '2026-11-12T16:30:00+00:00')",
        (ids["has_face.jpg"], _unit_vec(11)),
    )
    conn.commit()
    conn.close()

    ctx.reports_dir.mkdir(parents=True, exist_ok=True)
    (ctx.reports_dir / "stage4_insightface_checkpoint.json").write_text(
        json.dumps(
            {
                "scanned": 2,
                "total": 2,
                "faces_detected": 1,
                "updated_at": "2026-11-12T16:58:46+00:00",
            }
        ),
        encoding="utf-8",
    )

    conn_chk = get_connection(ctx.db_path)
    backfilled = backfill_legacy_face_scanned(conn_chk, ctx.reports_dir)
    conn_chk.commit()
    states = dict(conn_chk.execute("SELECT id, face_scanned FROM media_items").fetchall())
    conn_chk.close()

    assert backfilled == 2
    assert states[ids["has_face.jpg"]] == 1
    assert states[ids["landscape_no_face.jpg"]] == 1
    assert states[ids["future_new.jpg"]] == 0

    # If the future photo is also already marked scanned, Stage 4A skips completely without needing new work
    conn_w = get_connection(ctx.db_path)
    conn_w.execute("UPDATE media_items SET face_scanned = 1 WHERE id = ?", (ids["future_new.jpg"],))
    conn_w.commit()
    conn_w.close()

    scan_res = run_biometric_face_scan(ctx)
    assert scan_res["scanned"] == 0
    assert scan_res["already_scanned"] == 3


def test_consolidated_single_schema_drops_ai_manifest_and_syncs_stages(tmp_path: Path) -> None:
    arch = tmp_path / "ConsolidatedArchive"
    arch.mkdir(parents=True, exist_ok=True)
    # Create one dated photo and a visually identical re-saved unknown-date photo (different SHA-256, same dHash)
    arr = np.zeros((160, 160, 3), dtype=np.uint8)
    for x in range(160):
        arr[:, x, 0] = int((x / 159.0) * 255)
        arr[:, x, 1] = 120
        arr[:, x, 2] = 255 - int((x / 159.0) * 255)
    img = Image.fromarray(arr, mode="RGB")
    (arch / "dated").mkdir(parents=True)
    (arch / "unknown").mkdir(parents=True)
    img.save(arch / "dated" / "20200501_101500.jpg", format="JPEG", quality=95)
    img.save(arch / "unknown" / "mystery_shot.jpg", format="JPEG", quality=82)

    ctx = ArchiveContext(archive_root=arch, mode="in_place")
    run_stage1_consolidation(ctx)
    run_stage2_standardization(ctx, max_workers=2)
    res3 = run_stage3_validation(ctx, max_workers=2)
    assert res3["unknown_date_matches_found"] >= 1

    conn = get_connection(ctx.db_path, readonly=True)
    # No separate Stage-3 ai_manifest table exists in SQLite
    has_manifest_tbl = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ai_manifest'"
    ).fetchone()
    assert has_manifest_tbl is None

    # dHash match pointers are stored directly in unified `media_items`
    matched_row = conn.execute(
        "SELECT id, matched_dated_id, matched_timestamp, sha256_original FROM media_items WHERE original_filename = 'mystery_shot.jpg'"
    ).fetchone()
    assert matched_row is not None
    assert matched_row[1] is not None
    assert matched_row[2] == "2020-05-01T10:15:00"
    assert matched_row[3] is not None
    conn.close()

    # Simulate a legacy database that still carries `ai_manifest` + NULL `sha256_original`
    conn_legacy = get_connection(ctx.db_path)
    dated_id = conn_legacy.execute(
        "SELECT id FROM media_items WHERE original_filename = '20200501_101500.jpg'"
    ).fetchone()[0]
    unknown_id = matched_row[0]
    conn_legacy.execute(
        "UPDATE media_items SET matched_dated_id = NULL, matched_timestamp = NULL, sha256_original = NULL WHERE id = ?",
        (unknown_id,),
    )
    conn_legacy.execute(
        """
        CREATE TABLE ai_manifest (
            id TEXT PRIMARY KEY,
            matched_dated_id TEXT,
            matched_timestamp TEXT
        )
        """
    )
    conn_legacy.execute(
        "INSERT INTO ai_manifest (id, matched_dated_id, matched_timestamp) VALUES (?, ?, ?)",
        (unknown_id, dated_id, "2020-05-01T10:15:00"),
    )
    conn_legacy.commit()
    conn_legacy.close()

    init_db(ctx.db_path)
    conn_migrated = get_connection(ctx.db_path, readonly=True)
    assert conn_migrated.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ai_manifest'"
    ).fetchone() is None
    restored = conn_migrated.execute(
        "SELECT matched_dated_id, matched_timestamp, sha256_original FROM media_items WHERE id = ?",
        (unknown_id,),
    ).fetchone()
    assert restored[0] == dated_id
    assert restored[1] == "2020-05-01T10:15:00"
    assert restored[2] is not None
    conn_migrated.close()


def test_cross_stage_fts5_quarantine_and_standalone_server_sync(tmp_path: Path) -> None:
    arch = tmp_path / "SyncedMuseum"
    raw = arch / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    _make_jpg(raw / "portrait.jpg", (180, 120, 90))
    _make_jpg(raw / "worksheet.jpg", (230, 230, 230))
    (raw / "holiday_clip.avi").write_bytes(b"RIFF____AVI_CLIP_BYTES")

    ctx = ArchiveContext(archive_root=arch, mode="in_place")
    run_stage1_consolidation(ctx)
    run_stage2_standardization(ctx, max_workers=2)
    run_stage3_validation(ctx, max_workers=2)

    conn = get_connection(ctx.db_path)
    ids = dict(conn.execute("SELECT original_filename, id FROM media_items").fetchall())
    conn.execute(
        """
        INSERT INTO ai_descriptions (
            media_id, model_name, short_caption, detailed_description,
            tags, scene_type, is_document_or_homework, described_at
        ) VALUES
            (?, 'moondream', 'Outdoor portrait.', 'Person smiling outdoors', '["portrait"]', 'people', 0, '2026-01-01'),
            (?, 'moondream', 'Math worksheet.', 'Algebra problems on paper', '["homework"]', 'document/study', 1, '2026-01-01')
        """,
        (ids["portrait.jpg"], ids["worksheet.jpg"]),
    )
    conn.execute(
        """
        INSERT INTO people_faces (media_id, person_name, cluster_id, bounding_box, confidence, embedding, created_at)
        VALUES
            (?, NULL, 0, '[0.1,0.1,0.3,0.3]', 0.99, ?, '2026-01-01'),
            (?, 'Bob', 1, '[0.2,0.2,0.3,0.3]', 0.98, ?, '2026-01-01')
        """,
        (ids["portrait.jpg"], _unit_vec(1), ids["portrait.jpg"], _unit_vec(2)),
    )
    conn.commit()
    conn.close()

    cstats = cluster_and_generate_avatars(ctx, recluster=False)
    assert cstats["preserved_existing"] is True

    run_stage5_museum(ctx, overwrite_html=True)

    # Renaming cluster 0 -> Alice immediately syncs FTS5 + operation_history + catalog_data.js
    rename_person_clusters(ctx, [{"cluster_id": 0, "name": "Alice"}])
    conn_chk = get_connection(ctx.db_path, readonly=True)
    alice_hits = conn_chk.execute(
        "SELECT media_id FROM ai_search_fts WHERE ai_search_fts MATCH 'Alice'"
    ).fetchall()
    assert [r[0] for r in alice_hits] == [ids["portrait.jpg"]]
    vid_hits = conn_chk.execute(
        "SELECT media_id FROM ai_search_fts WHERE ai_search_fts MATCH 'holiday_clip'"
    ).fetchall()
    assert [r[0] for r in vid_hits] == [ids["holiday_clip.avi"]]
    ckpt_rows = dict(conn_chk.execute("SELECT stage, success_count FROM checkpoints").fetchall())
    assert "stage4_clustering" in ckpt_rows
    assert "stage5_museum" in ckpt_rows
    conn_chk.close()

    # Quarantine updates current_path, current_filename, updated_at, processing_inputs, and operation_history
    moved = quarantine_homework_files(ctx)
    assert moved == 1
    conn_q = get_connection(ctx.db_path, readonly=True)
    cur_p, cur_fn, proc_p = conn_q.execute(
        """
        SELECT m.current_path, m.current_filename, p.processing_path
        FROM media_items m
        JOIN processing_inputs p ON m.id = p.media_id
        WHERE m.id = ?
        """,
        (ids["worksheet.jpg"],),
    ).fetchone()
    assert "homework_archive/" in cur_p
    assert cur_p.endswith("/" + cur_fn)
    assert proc_p == cur_p
    q_ops = conn_q.execute(
        "SELECT COUNT(*) FROM operation_history WHERE media_id = ? AND action = 'QUARANTINE'",
        (ids["worksheet.jpg"],),
    ).fetchone()[0]
    assert q_ops == 1
    conn_q.close()

    # Standalone viewer server stays in parity on merge (preserves target name & rejects self-merge)
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), standalone_server.MuseumHandler)
    httpd.root_dir = ctx.archive_root  # type: ignore[attr-defined]
    httpd.db_path = ctx.db_path  # type: ignore[attr-defined]
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        merge_req = urllib.request.Request(
            f"{base}/api/people/merge",
            data=json.dumps({"source_id": 1, "target_id": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(merge_req, timeout=5) as resp:
            assert resp.status == 200
            assert json.loads(resp.read().decode("utf-8"))["ok"] is True

        conn_m = get_connection(ctx.db_path, readonly=True)
        names = {
            r[0]
            for r in conn_m.execute("SELECT person_name FROM people_faces WHERE cluster_id = 0").fetchall()
        }
        conn_m.close()
        assert names == {"Alice"}

        bad_req = urllib.request.Request(
            f"{base}/api/people/merge",
            data=json.dumps({"source_id": 0, "target_id": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(bad_req, timeout=5)
            assert False, "Expected 400 for self-merge"
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
    finally:
        httpd.shutdown()
        httpd.server_close()
