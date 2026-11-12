from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from memory_setup.biometrics.cluster import cluster_and_generate_avatars
from memory_setup.biometrics.detector import run_biometric_face_scan
from memory_setup.config import ArchiveContext
from memory_setup.db import get_connection
from memory_setup.ingestion.router import (
    run_stage1_consolidation,
    run_stage2_standardization,
    run_stage3_validation,
)
from memory_setup.setup.checker import check_disk_space
from memory_setup.viewer.builder import run_stage5_museum
from memory_setup.vision.ollama_client import get_llama_server_rss_mb, run_vision_pipeline


def get_archive_metrics(archive_root: Path) -> Dict[str, Any]:
    """Read live counts from `viewer/catalog.sqlite` in read-only mode without locking."""
    ctx = ArchiveContext(archive_root=archive_root)
    metrics: Dict[str, Any] = {
        "archive_root": str(ctx.archive_root),
        "has_db": ctx.db_path.exists(),
        "has_viewer": ctx.viewer_html_path.exists(),
        "free_disk_gb": round(check_disk_space(ctx.archive_root), 2),
        "total_items": 0,
        "photos": 0,
        "videos": 0,
        "documents": 0,
        "geocoded": 0,
        "faces_detected": 0,
        "people_clusters": 0,
        "ai_captioned": 0,
        "homework_flagged": 0,
    }
    if not ctx.db_path.exists():
        return metrics

    try:
        conn = get_connection(ctx.db_path, readonly=True)
        cur = conn.cursor()
        metrics["total_items"] = cur.execute("SELECT COUNT(*) FROM media_items").fetchone()[0]
        metrics["photos"] = cur.execute("SELECT COUNT(*) FROM media_items WHERE media_type = 'photo'").fetchone()[0]
        metrics["videos"] = cur.execute("SELECT COUNT(*) FROM media_items WHERE media_type = 'video'").fetchone()[0]
        metrics["documents"] = cur.execute("SELECT COUNT(*) FROM media_items WHERE media_type = 'document'").fetchone()[0]
        metrics["geocoded"] = cur.execute("SELECT COUNT(*) FROM metadata_exif WHERE country IS NOT NULL").fetchone()[0]
        metrics["faces_detected"] = cur.execute("SELECT COUNT(*) FROM people_faces").fetchone()[0]
        metrics["people_clusters"] = cur.execute(
            "SELECT COUNT(DISTINCT cluster_id) FROM people_faces WHERE cluster_id >= 0"
        ).fetchone()[0]
        metrics["ai_captioned"] = cur.execute("SELECT COUNT(*) FROM ai_descriptions").fetchone()[0]
        metrics["homework_flagged"] = cur.execute(
            "SELECT COUNT(*) FROM ai_descriptions WHERE is_document_or_homework = 1"
        ).fetchone()[0]
        conn.close()
    except Exception:
        pass
    return metrics


class PipelineManager:
    """Thread-safe orchestrator executing Stages 1-5 with live telemetry and cooperative stop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.active_ctx: Optional[ArchiveContext] = None
        self.state: Dict[str, Any] = {
            "running": False,
            "stage": "idle",
            "current": 0,
            "total": 0,
            "message": "Ready",
            "rate_img_s": 0.0,
            "started_at": None,
            "error": None,
            "results": {},
        }

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            snap = dict(self.state)
        snap["llama_rss_mb"] = get_llama_server_rss_mb()
        if self.active_ctx is not None:
            snap["archive"] = get_archive_metrics(self.active_ctx.archive_root)
        return snap

    def _on_progress(self, evt: Dict[str, Any]) -> None:
        with self._lock:
            self.state["stage"] = evt.get("stage", self.state["stage"])
            self.state["current"] = evt.get("current", 0)
            self.state["total"] = evt.get("total", 0)
            self.state["message"] = evt.get("message", "")
            if "rate_img_s" in evt:
                self.state["rate_img_s"] = evt["rate_img_s"]

    def start(
        self,
        ctx: ArchiveContext,
        stages: Optional[List[str]] = None,
    ) -> bool:
        with self._lock:
            if self.state["running"]:
                return False
            self.active_ctx = ctx
            self._stop_event.clear()
            self.state.update(
                {
                    "running": True,
                    "stage": "starting",
                    "current": 0,
                    "total": 0,
                    "message": "Initializing pipeline...",
                    "rate_img_s": 0.0,
                    "started_at": time.time(),
                    "error": None,
                    "results": {},
                }
            )
            self._thread = threading.Thread(
                target=self._run_worker,
                args=(ctx, stages or ["stage1", "stage2", "stage3", "stage4_faces", "stage4_vision", "stage5"]),
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            if self.state["running"]:
                self.state["message"] = "Stopping gracefully and flushing memory..."

    def _run_worker(self, ctx: ArchiveContext, stages: List[str]) -> None:
        results: Dict[str, Any] = {}
        try:
            if "stage1" in stages and not self._stop_event.is_set():
                self._on_progress({"stage": "stage1", "current": 0, "total": 1, "message": "Stage 1: Consolidating & pairing files..."})
                results["stage1"] = run_stage1_consolidation(ctx, progress_cb=self._on_progress)

            if "stage2" in stages and not self._stop_event.is_set():
                self._on_progress({"stage": "stage2", "current": 0, "total": 1, "message": "Stage 2: Extracting EXIF, geocoding & generating proxies..."})
                results["stage2"] = run_stage2_standardization(ctx, progress_cb=self._on_progress)

            if "stage3" in stages and not self._stop_event.is_set():
                self._on_progress({"stage": "stage3", "current": 0, "total": 1, "message": "Stage 3: Validating images & computing perceptual hashes..."})
                results["stage3"] = run_stage3_validation(ctx, progress_cb=self._on_progress)

            if "stage4_faces" in stages and ctx.enable_biometrics and not self._stop_event.is_set():
                self._on_progress({"stage": "stage4_faces", "current": 0, "total": 1, "message": "Stage 4A: Detecting faces & clustering family identities..."})
                scan_res = run_biometric_face_scan(ctx, progress_cb=self._on_progress)
                cluster_res = cluster_and_generate_avatars(ctx)
                results["stage4_faces"] = {"scan": scan_res, "clustering": cluster_res}

            if "stage4_vision" in stages and ctx.enable_vision and not self._stop_event.is_set():
                self._on_progress({"stage": "stage4_vision", "current": 0, "total": 1, "message": "Stage 4B: Running memory-bounded local Vision AI..."})
                results["stage4_vision"] = run_vision_pipeline(
                    ctx,
                    stop_event=self._stop_event,
                    progress_cb=self._on_progress,
                )

            if "stage5" in stages:
                self._on_progress({"stage": "stage5", "current": 0, "total": 1, "message": "Stage 5: Indexing FTS5 search & building Offline Museum..."})
                results["stage5"] = run_stage5_museum(ctx)

            with self._lock:
                self.state["results"] = results
                self.state["stage"] = "paused" if self._stop_event.is_set() else "complete"
                self.state["message"] = (
                    "Paused cleanly (progress saved)."
                    if self._stop_event.is_set()
                    else "All selected pipeline stages completed!"
                )
        except Exception as exc:
            with self._lock:
                self.state["error"] = str(exc)
                self.state["stage"] = "error"
                self.state["message"] = f"Error: {exc}"
        finally:
            with self._lock:
                self.state["running"] = False
