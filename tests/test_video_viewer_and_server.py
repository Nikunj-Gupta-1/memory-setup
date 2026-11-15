from __future__ import annotations

import json
import socketserver
import threading
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from memory_setup.config import ArchiveContext
from memory_setup.db import get_connection
from memory_setup.gui.app import create_app_server
from memory_setup.ingestion.router import (
    run_stage1_consolidation,
    run_stage2_standardization,
    run_stage3_validation,
)
from memory_setup.viewer.builder import quarantine_homework_files, run_stage5_museum
from memory_setup.viewer.template import server as standalone_server


def _build_archive_with_videos(tmp_path: Path) -> ArchiveContext:
    arch = tmp_path / "VideoMuseum"
    raw = arch / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    img_clean = Image.new("RGB", (200, 150), color=(30, 120, 200))
    img_clean.save(raw / "family.jpg", format="JPEG")
    img_hw = Image.new("RGB", (200, 150), color=(220, 220, 220))
    img_hw.save(raw / "homework_page.jpg", format="JPEG")

    # Paired Live Photo video + legacy .avi video
    (raw / "family.mov").write_bytes(b"0123456789ABCDEFmovpayload")
    (raw / "legacy_cam.avi").write_bytes(b"RIFF____AVI_PAYLOAD_BYTES")

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
            (?, 'moondream', 'Family outdoors.', 'Family portrait outdoors', '["family"]', 'outdoor', 0, '2026-01-01'),
            (?, 'moondream', 'Homework sheet.', 'Algebra worksheet', '["homework"]', 'document/study', 1, '2026-01-01')
        """,
        (ids["family.jpg"], ids["homework_page.jpg"]),
    )
    conn.commit()
    conn.close()
    return ctx


def test_catalog_exports_all_videos_and_quarantine_updates_reused_original(tmp_path: Path) -> None:
    ctx = _build_archive_with_videos(tmp_path)
    run_stage5_museum(ctx, overwrite_html=True)

    js_text = ctx.catalog_data_js_path.read_text(encoding="utf-8")
    videos_line = next(line for line in js_text.splitlines() if line.startswith("const ALL_VIDEOS = "))
    videos = json.loads(videos_line[len("const ALL_VIDEOS = ") :].rstrip(";"))

    assert len(videos) == 2
    by_ext = {v["ext"]: v for v in videos}
    assert ".mov" in by_ext and ".avi" in by_ext
    assert by_ext[".mov"]["live"] is True
    assert by_ext[".avi"]["live"] is False
    assert by_ext[".avi"]["src"].startswith("../content/")

    viewer_html = ctx.viewer_html_path.read_text(encoding="utf-8")
    for marker in (
        "ALL_VIDEOS",
        "tab-videos",
        "catalog_data.js",
        "/api/open-media",
        "openVideoLightbox",
        "toggleTheme",
        "Read-Only Exhibition View",
        "handleHomeworkFilter",
        "renderNextHomeworkBatch",
        "handleHomeworkScroll",
        "homeworkLoadMoreRow",
        "hwSearchInput",
    ):
        assert marker in viewer_html
    for forbidden in ("peopleManagerModal", "cleanup_homework.sh", "commit_merges.py", "quarantineHomeworkLive", "openPeopleManager"):
        assert forbidden not in viewer_html

    moved = quarantine_homework_files(ctx)
    assert moved == 1

    conn = get_connection(ctx.db_path, readonly=True)
    cur_p, proc_p = conn.execute(
        """
        SELECT m.current_path, p.processing_path
        FROM media_items m
        JOIN processing_inputs p ON m.id = p.media_id
        JOIN ai_descriptions a ON m.id = a.media_id
        WHERE a.is_document_or_homework = 1
        """
    ).fetchone()
    conn.close()

    assert "homework_archive/" in cur_p
    assert proc_p == cur_p
    assert ctx.resolve(cur_p).exists()
    assert quarantine_homework_files(ctx) == 0


def test_standalone_museum_server_range_streaming_and_open_media_security(tmp_path: Path) -> None:
    ctx = _build_archive_with_videos(tmp_path)
    run_stage5_museum(ctx, overwrite_html=True)

    conn = get_connection(ctx.db_path, readonly=True)
    mov_rel, avi_rel = conn.execute(
        """
        SELECT
            MAX(CASE WHEN original_ext = '.mov' THEN current_path END),
            MAX(CASE WHEN original_ext = '.avi' THEN current_path END)
        FROM media_items
        """
    ).fetchone()
    conn.close()

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), standalone_server.MuseumHandler)
    httpd.root_dir = ctx.archive_root  # type: ignore[attr-defined]
    httpd.db_path = ctx.db_path  # type: ignore[attr-defined]
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{port}"
        mov_req = urllib.request.Request(f"{base}/{mov_rel}", headers={"Range": "bytes=0-9"})
        with urllib.request.urlopen(mov_req, timeout=5) as resp:
            assert resp.status == 206
            assert resp.headers.get("Accept-Ranges") == "bytes"
            assert resp.headers.get("Content-Range") == "bytes 0-9/26"
            assert resp.read() == b"0123456789"

        # HEAD request succeeds
        head_req = urllib.request.Request(f"{base}/{mov_rel}", method="HEAD")
        with urllib.request.urlopen(head_req, timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers.get("Accept-Ranges") == "bytes"
            assert resp.read() == b""

        # Invalid range returns 416
        bad_range = urllib.request.Request(f"{base}/{mov_rel}", headers={"Range": "bytes=99999-"})
        try:
            urllib.request.urlopen(bad_range, timeout=5)
            assert False, "Expected 416 Range Not Satisfiable"
        except urllib.error.HTTPError as exc:
            assert exc.code == 416

        # Traversal outside content/ rejected with 403
        for forbidden_path in ("../../etc/passwd", "../viewer/catalog.sqlite"):
            req = urllib.request.Request(
                f"{base}/api/open-media",
                data=json.dumps({"path": forbidden_path}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=5)
                assert False, f"Expected 403 for {forbidden_path}"
            except urllib.error.HTTPError as exc:
                assert exc.code == 403

        with patch.object(standalone_server, "_open_with_os_player") as mock_open:
            open_req = urllib.request.Request(
                f"{base}/api/open-media",
                data=json.dumps({"path": f"../{avi_rel}"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(open_req, timeout=5) as resp:
                assert resp.status == 200
                payload = json.loads(resp.read().decode("utf-8"))
                assert payload["ok"] is True
                mock_open.assert_called_once()
                assert mock_open.call_args[0][0] == ctx.resolve(avi_rel).resolve()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_gui_server_range_streaming_and_open_media(tmp_path: Path) -> None:
    ctx = _build_archive_with_videos(tmp_path)
    run_stage5_museum(ctx, overwrite_html=True)

    conn = get_connection(ctx.db_path, readonly=True)
    avi_rel = conn.execute("SELECT current_path FROM media_items WHERE original_ext = '.avi'").fetchone()[0]
    conn.close()

    server = create_app_server(default_archive=ctx.archive_root, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{port}"
        req = urllib.request.Request(f"{base}/archive/{avi_rel}", headers={"Range": "bytes=4-11"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 206
            assert resp.headers.get("Content-Range") == "bytes 4-11/25"
            assert resp.read() == b"____AVI_"

        with patch("memory_setup.gui.app._open_with_os_player") as mock_open:
            open_req = urllib.request.Request(
                f"{base}/api/open-media",
                data=json.dumps({"path": f"../{avi_rel}"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(open_req, timeout=5) as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
                assert data["ok"] is True
                mock_open.assert_called_once()
    finally:
        server.shutdown()
        server.server_close()


def test_standalone_server_password_gate_search_and_readonly(tmp_path: Path) -> None:
    import http.cookiejar

    ctx = _build_archive_with_videos(tmp_path)
    run_stage5_museum(ctx, overwrite_html=True)

    conn = get_connection(ctx.db_path, readonly=True)
    avi_rel = conn.execute("SELECT current_path FROM media_items WHERE original_ext = '.avi'").fetchone()[0]
    conn.close()

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), standalone_server.MuseumHandler)
    httpd.root_dir = ctx.archive_root  # type: ignore[attr-defined]
    httpd.db_path = ctx.db_path  # type: ignore[attr-defined]
    httpd.museum_password = "family-secret-pass"  # type: ignore[attr-defined]
    httpd.museum_read_only = True  # type: ignore[attr-defined]
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{port}"

        # Unauthenticated page and API requests must return 401 with strict zero-content CSP
        try:
            urllib.request.urlopen(f"{base}/viewer/viewer.html", timeout=5)
            assert False, "Expected 401 before login"
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
            csp = exc.headers.get("Content-Security-Policy", "")
            assert "default-src 'none'" in csp
            locked_body = exc.read()
            assert b"/__museum_login" in locked_body
            assert b"catalog_data.js" not in locked_body
            assert b"ALL_PHOTOS" not in locked_body

        try:
            urllib.request.urlopen(f"{base}/viewer/catalog_data.js", timeout=5)
            assert False, "Expected 401 for unauthenticated catalog_data.js"
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        try:
            urllib.request.urlopen(f"{base}/api/search?q=Family", timeout=5)
            assert False, "Expected 401 for unauthenticated /api/search"
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        # Wrong password rejected
        bad_login = urllib.request.Request(
            f"{base}/__museum_login",
            data=json.dumps({"password": "wrong"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(bad_login, timeout=5)
            assert False, "Expected 401 for wrong password"
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        # Valid login via JSON sets signed HttpOnly cookie
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        good_login = urllib.request.Request(
            f"{base}/__museum_login",
            data=json.dumps({"password": "family-secret-pass"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(good_login, timeout=5) as resp:
            assert resp.status == 200
            assert json.loads(resp.read().decode("utf-8"))["ok"] is True

        # Authenticated viewer, FTS5 search, and Range video streaming succeed
        with opener.open(f"{base}/viewer/viewer.html", timeout=5) as resp:
            assert resp.status == 200
            assert b"ALL_PHOTOS" in resp.read() or b"Offline Museum" in resp.read()

        with opener.open(f"{base}/api/search?q=Family", timeout=5) as resp:
            assert resp.status == 200
            search_data = json.loads(resp.read().decode("utf-8"))
            assert search_data["ok"] is True
            assert any(r["filename"] == "family.jpg" for r in search_data["results"])

        rng_req = urllib.request.Request(f"{base}/{avi_rel}", headers={"Range": "bytes=0-3"})
        with opener.open(rng_req, timeout=5) as resp:
            assert resp.status == 206
            assert resp.read() == b"RIFF"

        # Raw SQLite file and share_config.json are always blocked even when authenticated
        for forbidden_rel in ("/viewer/catalog.sqlite", "/viewer/share_config.json"):
            try:
                opener.open(f"{base}{forbidden_rel}", timeout=5)
                assert False, f"Expected 403 for direct download of {forbidden_rel}"
            except urllib.error.HTTPError as exc:
                assert exc.code == 403

        # Read-only share mode rejects POST /api/open-media
        open_req = urllib.request.Request(
            f"{base}/api/open-media",
            data=json.dumps({"path": f"../{avi_rel}"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            opener.open(open_req, timeout=5)
            assert False, "Expected 403 in read-only share mode"
        except urllib.error.HTTPError as exc:
            assert exc.code == 403

        # Clicking Lock triggers /__museum_logout which clears session and re-locks immediately
        try:
            opener.open(f"{base}/__museum_logout", timeout=5)
            assert False, "Expected redirect back to locked 401 viewer"
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        try:
            opener.open(f"{base}/viewer/viewer.html", timeout=5)
            assert False, "Expected viewer to remain locked after /__museum_logout"
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_museum_server_loads_password_from_share_config(tmp_path: Path) -> None:
    import http.cookiejar

    ctx = _build_archive_with_videos(tmp_path)
    run_stage5_museum(ctx, overwrite_html=True)
    (ctx.viewer_dir / "share_config.json").write_text(
        json.dumps({"password": "config-file-password", "port": 8888}),
        encoding="utf-8",
    )

    import importlib.util

    server_py = ctx.viewer_dir / "server.py"
    spec = importlib.util.spec_from_file_location("standalone_museum_server_cfg", server_py)
    assert spec and spec.loader
    standalone_server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(standalone_server)

    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), standalone_server.MuseumHandler)
    httpd.root_dir = ctx.archive_root  # type: ignore[attr-defined]
    httpd.db_path = ctx.db_path  # type: ignore[attr-defined]
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        try:
            urllib.request.urlopen(f"{base}/viewer/viewer.html", timeout=5)
            assert False, "Expected 401 when share_config.json defines password"
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        try:
            urllib.request.urlopen(f"{base}/__museum_ping", timeout=5)
            assert False, "Expected 401 for unauthenticated /__museum_ping"
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        login = urllib.request.Request(
            f"{base}/__museum_login",
            data=json.dumps({"password": "config-file-password"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(login, timeout=5) as resp:
            assert resp.status == 200
        with opener.open(f"{base}/viewer/viewer.html", timeout=5) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            assert "lockMuseumBtn" in html
            assert "/__museum_logout" in html
            assert "initLiveSessionGuard" in html
            assert "triggerRemoteLogout" in html
            assert "/__museum_events" in html
            assert "/__museum_ping" in html
            assert "setupTouchSwipe" in html
            assert "@media (max-width: 640px)" in html

        with opener.open(f"{base}/__museum_ping", timeout=5) as resp:
            assert resp.status == 200
            assert json.loads(resp.read().decode("utf-8"))["ok"] is True

        # Setting shutdown event immediately forces /__museum_ping to return 401
        standalone_server._get_shutdown_event(httpd).set()
        try:
            opener.open(f"{base}/__museum_ping", timeout=5)
            assert False, "Expected 401 after shutdown event is set"
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
    finally:
        httpd.shutdown()
        httpd.server_close()
