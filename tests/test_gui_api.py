from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

from PIL import Image

from memory_setup.config import ArchiveContext
from memory_setup.db import get_connection
from memory_setup.gui.app import create_app_server
from memory_setup.ingestion.router import (
    run_stage1_consolidation,
    run_stage2_standardization,
    run_stage3_validation,
)
from memory_setup.viewer.builder import run_stage5_museum


def _post_json(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_gui_server_endpoints_and_museum_mount(tmp_path: Path) -> None:
    arch = tmp_path / "GuiArchive"
    (arch / "raw").mkdir(parents=True)
    img = Image.new("RGB", (120, 120), color=(180, 100, 40))
    img.save(arch / "raw" / "sample.jpg", format="JPEG")

    ctx = ArchiveContext(archive_root=arch, mode="in_place")
    run_stage1_consolidation(ctx)
    run_stage2_standardization(ctx, max_workers=1)
    run_stage3_validation(ctx, max_workers=1)
    conn = get_connection(ctx.db_path)
    mid = conn.execute("SELECT id FROM media_items ORDER BY id ASC LIMIT 1").fetchone()[0]
    conn.execute(
        """
        INSERT INTO people_faces (media_id, person_name, cluster_id, bounding_box, confidence, embedding, created_at)
        VALUES
            (?, NULL, 0, '[1, 1, 20, 20]', 0.99, ?, '2026-01-01'),
            (?, NULL, 1, '[30, 30, 50, 50]', 0.99, ?, '2026-01-01')
        """,
        (mid, b"\x00" * 64, mid, b"\x00" * 64),
    )
    conn.commit()
    conn.close()
    run_stage5_museum(ctx)

    server = create_app_server(default_archive=arch, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(f"{base}/api/status", timeout=5) as r:
            status = json.loads(r.read().decode("utf-8"))
        assert status["archive"]["total_items"] == 1

        preflight = _post_json(f"{base}/api/preflight", {"archive_path": str(arch), "mode": "in_place"})
        assert "tools" in preflight and "disk" in preflight

        safe_pre = _post_json(
            f"{base}/api/preflight",
            {"archive_path": str(arch), "mode": "safe_copy", "source_dirs": [str(arch / "content")]},
        )
        assert safe_pre["disk"]["mode"] == "safe_copy"

        q_res = _post_json(f"{base}/api/homework/quarantine", {"archive_path": str(arch)})
        assert q_res["ok"] is True

        ren = _post_json(f"{base}/api/people/rename", {"updates": [{"cluster_id": 0, "name": "Nikunj"}]})
        assert ren["ok"] is True

        mrg = _post_json(f"{base}/api/people/merge", {"source_id": 1, "target_id": 0})
        assert mrg["ok"] is True

        # Live mounted museum viewer HTML
        with urllib.request.urlopen(f"{base}/archive/viewer/viewer.html", timeout=5) as r:
            assert r.status == 200
            html = r.read().decode("utf-8")
            assert "<html" in html.lower()
    finally:
        server.shutdown()
        server.server_close()
