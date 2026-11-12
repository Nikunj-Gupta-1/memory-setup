from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

from memory_setup.config import ArchiveContext
from memory_setup.db import get_connection
from memory_setup.ingestion.router import (
    run_stage1_consolidation,
    run_stage2_standardization,
    run_stage3_validation,
)


def _create_sample_image(path: Path, color: tuple[int, int, int], size=(320, 240)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color=color)
    img.save(path, format="JPEG", quality=90)


def test_stages_1_to_3_in_place_and_deduplication(tmp_path: Path) -> None:
    archive = tmp_path / "ArchiveInPlace"
    _create_sample_image(archive / "Vacation" / "IMG_001.jpg", (220, 40, 40))
    # Live Photo companion .mov with matching stem
    (archive / "Vacation" / "IMG_001.mov").write_bytes(b"\x00\x00\x00\x20ftypqt  \x00\x00\x00\x00")
    # Exact byte duplicate of IMG_001.jpg
    shutil.copy2(archive / "Vacation" / "IMG_001.jpg", archive / "Duplicates" / "copy_of_001.jpg") if (archive / "Duplicates").mkdir(parents=True, exist_ok=True) or True else None
    # Distinct second image
    _create_sample_image(archive / "Family" / "IMG_002.jpg", (40, 140, 220))

    ctx = ArchiveContext(archive_root=archive, mode="in_place")
    res1 = run_stage1_consolidation(ctx)
    assert res1["success_count"] == 3  # 2 unique photos + 1 .mov
    assert res1["duplicates_skipped"] == 1

    res2 = run_stage2_standardization(ctx, max_workers=2)
    assert res2["total_photos"] == 2
    assert res2["processing_inputs_ready"] == 2

    res3 = run_stage3_validation(ctx, max_workers=2)
    assert res3["valid_images"] == 2
    assert res3["corrupt_images"] == 0
    assert ctx.manifest_json_path.exists()
    assert ctx.manifest_csv_path.exists()

    conn = get_connection(ctx.db_path, readonly=True)
    rows = conn.execute("SELECT id, media_type, current_path FROM media_items ORDER BY id ASC").fetchall()
    assert len(rows) == 3
    assert [r[0] for r in rows] == ["P_000001", "P_000002", "V_000001"]
    # All files live cleanly inside content/
    for _, _, rel in rows:
        assert rel.startswith("content/")
        assert (archive / rel).exists()

    # Check dhash & processing_inputs populated cleanly without leading double viewer/viewer prefix
    p_rows = conn.execute("SELECT processing_path, dhash FROM processing_inputs").fetchall()
    assert len(p_rows) == 2
    for p_rel, dhash in p_rows:
        assert p_rel.startswith("viewer/.processing_cache/") or p_rel.startswith("content/")
        assert not p_rel.startswith("viewer/viewer/")
        assert len(dhash) == 16
    conn.close()


def test_stage1_safe_copy_leaves_sources_untouched(tmp_path: Path) -> None:
    raw_src = tmp_path / "SD_Card"
    target_arch = tmp_path / "MuseumArchive"
    _create_sample_image(raw_src / "DCIM" / "DSC0001.jpg", (90, 200, 110))

    ctx = ArchiveContext(archive_root=target_arch, source_dirs=[raw_src], mode="safe_copy")
    res1 = run_stage1_consolidation(ctx)
    assert res1["success_count"] == 1

    # Original file remains untouched on source drive
    assert (raw_src / "DCIM" / "DSC0001.jpg").exists()
    # Target copy exists inside target_arch/content/images/UNKNOWN_DATE/
    assert (target_arch / "content" / "images" / "UNKNOWN_DATE" / "UNKNOWN_DATE__P_000001.jpg").exists()
