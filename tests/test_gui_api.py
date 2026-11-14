from __future__ import annotations

import json
import sys
import threading
import time
import types
import urllib.request
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from memory_setup.config import ArchiveContext
from memory_setup.db import get_connection
from memory_setup.gui.app import (
    _shutdown_control_center,
    create_app_server,
    get_window_icon_path,
    launch_gui,
)
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

        # App icon asset served cleanly
        with urllib.request.urlopen(f"{base}/icon.png", timeout=5) as r:
            assert r.status == 200
            assert len(r.read()) > 100
    finally:
        server.shutdown()
        server.server_close()


def test_gui_server_sse_closes_promptly_on_shutdown(tmp_path: Path) -> None:
    server = create_app_server(default_archive=tmp_path, host="127.0.0.1", port=0)
    assert server.daemon_threads is True
    assert server.block_on_close is False

    port = server.server_address[1]
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.1},
        daemon=True,
    )
    thread.start()

    sse_started = threading.Event()
    sse_ended = threading.Event()

    def _consume_sse() -> None:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/pipeline/events", timeout=5) as resp:
            first_line = resp.readline()
            if first_line:
                sse_started.set()
            while resp.read(64):
                pass
        sse_ended.set()

    client_thread = threading.Thread(target=_consume_sse, daemon=True)
    client_thread.start()
    assert sse_started.wait(timeout=2.0)

    t0 = time.monotonic()
    _shutdown_control_center(server)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.5
    assert sse_ended.wait(timeout=1.5)


def test_window_icon_path_per_platform() -> None:
    win_icon = get_window_icon_path("Windows")
    mac_icon = get_window_icon_path("Darwin")
    linux_icon = get_window_icon_path("Linux")
    assert win_icon is not None and win_icon.name == "icon.ico"
    assert mac_icon is not None and mac_icon.name == "icon.png"
    assert linux_icon is not None and linux_icon.name == "icon.png"


def test_launch_gui_uses_ico_on_windows_and_cleans_up_sse(tmp_path: Path) -> None:
    class _Event:
        def __init__(self) -> None:
            self.handlers = []

        def __iadd__(self, fn):
            self.handlers.append(fn)
            return self

        def fire(self) -> bool:
            return any(fn() is False for fn in self.handlers)

    captured: dict = {}
    sse_ready = threading.Event()

    class _FakeWindow:
        def __init__(self, url: str) -> None:
            self.url = url
            self.events = types.SimpleNamespace(closing=_Event(), closed=_Event())

    fake_webview = types.ModuleType("webview")

    def _fake_create_window(title: str, url: str, width: int = 800, height: int = 600):
        win = _FakeWindow(url)
        captured["window"] = win
        captured["url"] = url
        return win

    def _fake_start(**kwargs):
        captured["start_kwargs"] = kwargs
        win = captured["window"]

        def _hold_sse() -> None:
            with urllib.request.urlopen(f"{win.url}api/pipeline/events", timeout=5) as resp:
                resp.readline()
                sse_ready.set()
                while resp.read(64):
                    pass

        t = threading.Thread(target=_hold_sse, daemon=True)
        t.start()
        assert sse_ready.wait(timeout=2.0)
        assert not win.events.closing.fire()
        win.events.closed.fire()

    fake_webview.create_window = _fake_create_window  # type: ignore[attr-defined]
    fake_webview.start = _fake_start  # type: ignore[attr-defined]

    with (
        patch.dict(sys.modules, {"webview": fake_webview}),
        patch("memory_setup.gui.app.platform.system", return_value="Windows"),
    ):
        t0 = time.monotonic()
        launch_gui(default_archive=tmp_path, port=0, use_webview=True, open_browser=False)
        elapsed = time.monotonic() - t0

    assert elapsed < 2.0
    assert Path(captured["start_kwargs"]["icon"]).name == "icon.ico"
