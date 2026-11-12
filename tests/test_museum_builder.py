from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from memory_setup.config import ArchiveContext
from memory_setup.db import get_connection
from memory_setup.ingestion.router import (
    run_stage1_consolidation,
    run_stage2_standardization,
    run_stage3_validation,
)
from memory_setup.viewer.builder import (
    merge_person_clusters,
    quarantine_homework_files,
    rename_person_clusters,
    run_stage5_museum,
)


def _setup_synthetic_archive(tmp_path: Path) -> ArchiveContext:
    arch = tmp_path / "MuseumTest"
    (arch / "raw").mkdir(parents=True, exist_ok=True)
    for idx, color in enumerate([(200, 60, 60), (60, 200, 60), (60, 60, 200)], start=1):
        img = Image.new("RGB", (200, 200), color=color)
        img.save(arch / "raw" / f"photo_{idx}.jpg", format="JPEG")

    ctx = ArchiveContext(archive_root=arch, mode="in_place")
    run_stage1_consolidation(ctx)
    run_stage2_standardization(ctx, max_workers=2)
    run_stage3_validation(ctx, max_workers=2)

    conn = get_connection(ctx.db_path)
    ids = [r[0] for r in conn.execute("SELECT id FROM media_items ORDER BY id ASC").fetchall()]
    assert len(ids) == 3

    # Seed AI descriptions and 2 people clusters
    conn.execute(
        """
        INSERT INTO ai_descriptions (
            media_id, model_name, short_caption, detailed_description, tags, scene_type, is_document_or_homework, described_at
        )
        VALUES
            (?, 'moondream', 'Family picnic.', 'Family picnic in the park with cake', '["picnic","outdoor"]', 'outdoor', 0, '2026-01-01'),
            (?, 'moondream', 'Math notebook.', 'Handwritten math homework notebook page', '["homework","notebook"]', 'document/study', 1, '2026-01-01'),
            (?, 'moondream', 'Dubai Marina.', 'Sunset skyline over Dubai Marina', '["sunset","skyline"]', 'outdoor', 0, '2026-01-01')
        """,
        (ids[0], ids[1], ids[2]),
    )
    conn.execute(
        """
        INSERT INTO people_faces (media_id, person_name, cluster_id, bounding_box, confidence, embedding, created_at)
        VALUES
            (?, NULL, 0, '[10, 10, 50, 50]', 0.99, ?, '2026-01-01'),
            (?, NULL, 1, '[15, 15, 60, 60]', 0.98, ?, '2026-01-01')
        """,
        (ids[0], b"\x00" * 64, ids[2], b"\x00" * 64),
    )
    conn.commit()
    conn.close()
    return ctx


def test_stage5_museum_anti_clobber_and_js_contract(tmp_path: Path) -> None:
    ctx = _setup_synthetic_archive(tmp_path)
    ctx.ensure_layout()

    # Pre-create a customized viewer.html to test anti-clobber protection
    sentinel_html = "<!-- CUSTOMIZED MUSEUM UI -->"
    ctx.viewer_html_path.write_text(sentinel_html, encoding="utf-8")

    summary = run_stage5_museum(ctx, overwrite_html=False)
    assert summary["fts_indexed_count"] == 3
    assert summary["viewer_html_written"] is False
    assert ctx.viewer_html_path.read_text(encoding="utf-8") == sentinel_html

    # Verify FTS5 full-text query works
    conn = get_connection(ctx.db_path, readonly=True)
    ids = [r[0] for r in conn.execute("SELECT id FROM media_items ORDER BY id ASC").fetchall()]
    hits = conn.execute("SELECT media_id FROM ai_search_fts WHERE ai_search_fts MATCH 'Dubai'").fetchall()
    assert [r[0] for r in hits] == [ids[2]]
    conn.close()

    # Verify JS contract: p.f must be list of ints
    js_text = ctx.catalog_data_js_path.read_text(encoding="utf-8")
    for line in js_text.splitlines():
        if line.startswith("const ALL_PHOTOS = "):
            data = json.loads(line[len("const ALL_PHOTOS = ") :].rstrip(";"))
            item1 = next(x for x in data if x["id"] == ids[0])
            assert item1["f"] == [0]
            assert isinstance(item1["f"][0], int)

    # Rename & merge people clusters and verify persistence
    rename_person_clusters(ctx, [{"cluster_id": 0, "name": "Alice"}])
    merge_person_clusters(ctx, source_id=1, target_id=0)

    js_after = ctx.catalog_data_js_path.read_text(encoding="utf-8")
    assert '"name": "Alice"' in js_after

    # Quarantine homework file -> moves photo 2 into content/Documents/homework_archive/
    moved = quarantine_homework_files(ctx)
    assert moved == 1
    conn2 = get_connection(ctx.db_path, readonly=True)
    new_path = conn2.execute("SELECT current_path FROM media_items WHERE id = ?", (ids[1],)).fetchone()[0]
    conn2.close()
    assert "homework_archive" in new_path
    assert ctx.resolve(new_path).exists()
