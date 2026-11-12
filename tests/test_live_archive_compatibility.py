from __future__ import annotations

from pathlib import Path
import pytest

from memory_setup.config import ArchiveContext
from memory_setup.pipeline_runner import get_archive_metrics


LIVE_ARCHIVE = Path("/Users/nikunjgupta/Downloads/images")


@pytest.mark.skipif(not (LIVE_ARCHIVE / "viewer" / "catalog.sqlite").exists(), reason="Live reference archive not present")
def test_live_archive_readonly_compatibility() -> None:
    metrics = get_archive_metrics(LIVE_ARCHIVE)
    assert metrics["has_db"] is True
    assert metrics["has_viewer"] is True
    assert metrics["total_items"] >= 33000
    assert metrics["faces_detected"] >= 60000

    ctx = ArchiveContext(archive_root=LIVE_ARCHIVE)
    # Path normalization should resolve inside archive cleanly
    sample = ctx.resolve("viewer/catalog.sqlite")
    assert sample.exists()
