from __future__ import annotations

import base64
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from memory_setup.config import (
    ArchiveContext,
    OLLAMA_DEFAULT_MODEL,
    OLLAMA_DEFAULT_URL,
    OLLAMA_NUM_CTX,
    OLLAMA_NUM_PREDICT,
    OLLAMA_RSS_LIMIT_MB,
)
from memory_setup.db import get_connection
from memory_setup.vision.classifier import classify_description

DEFAULT_PROMPT = (
    "Describe this image in detail, including all people, objects, activities, text, and setting."
)

ProgressCallback = Optional[Callable[[Dict[str, Any]], None]]


def get_llama_server_rss_mb() -> Optional[float]:
    """Return current RSS memory (MB) of any running llama-server process, if psutil is available."""
    try:
        import psutil  # type: ignore

        total_bytes = 0
        found = False
        for proc in psutil.process_iter(["name", "memory_info"]):
            name = (proc.info.get("name") or "").lower()
            if "llama-server" in name:
                mem = proc.info.get("memory_info")
                if mem:
                    total_bytes += mem.rss
                    found = True
        return (total_bytes / (1024 * 1024)) if found else None
    except Exception:
        return None


class MemoryBoundedOllamaClient:
    """
    Strictly single-worker Ollama VLM client with bounded context, cache_prompt=False,
    and periodic keep_alive=0 runner recycling to keep memory under 2.5 GB.
    """

    def __init__(
        self,
        base_url: str = OLLAMA_DEFAULT_URL,
        model: str = OLLAMA_DEFAULT_MODEL,
        num_ctx: int = OLLAMA_NUM_CTX,
        num_predict: int = OLLAMA_NUM_PREDICT,
        recycle_interval: int = 200,
        rss_limit_mb: float = OLLAMA_RSS_LIMIT_MB,
        timeout: int = 60,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.recycle_interval = max(1, recycle_interval)
        self.rss_limit_mb = rss_limit_mb
        self.timeout = timeout
        self._requests_since_recycle = 0

    def build_generate_payload(self, image_b64: str, prompt: str = DEFAULT_PROMPT) -> Dict[str, Any]:
        return {
            "model": self.model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
            "cache_prompt": False,
            "options": {
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict,
                "temperature": 0,
            },
            "keep_alive": "5m",
        }

    def recycle_runner(self) -> bool:
        """Send keep_alive=0 to unload llama-server and flush host prompt cache & Metal buffers."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={"model": self.model, "keep_alive": 0},
                timeout=10,
            )
            self._requests_since_recycle = 0
            time.sleep(0.2)
            return resp.status_code == 200
        except Exception:
            return False

    def describe_image(self, img_path: Path, prompt: str = DEFAULT_PROMPT) -> Optional[str]:
        """Describe a single image synchronously (strictly 1 image at a time)."""
        with open(img_path, "rb") as fp:
            image_b64 = base64.b64encode(fp.read()).decode("utf-8")

        payload = self.build_generate_payload(image_b64, prompt=prompt)
        resp = requests.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "").strip()

        self._requests_since_recycle += 1
        if self._requests_since_recycle >= self.recycle_interval:
            self.recycle_runner()
        else:
            rss = get_llama_server_rss_mb()
            if rss is not None and rss > self.rss_limit_mb:
                self.recycle_runner()

        return text or None


def run_vision_pipeline(
    ctx: ArchiveContext,
    limit: Optional[int] = None,
    batch_size: int = 10,
    stop_event: Optional[threading.Event] = None,
    progress_cb: ProgressCallback = None,
) -> Dict[str, Any]:
    """
    Execute Stage 4B/4C: Single-worker memory-bounded local VLM captioning and document flagging.
    Supports cooperative pause/stop via `stop_event` with guaranteed runner teardown.
    """
    conn = get_connection(ctx.db_path)
    cursor = conn.cursor()

    rows = cursor.execute(
        """
        SELECT p.media_id, p.processing_path, e.city, e.country
        FROM processing_inputs p
        LEFT JOIN metadata_exif e ON p.media_id = e.media_id
        ORDER BY p.media_id ASC
        """
    ).fetchall()

    already_described = set(
        r[0] for r in cursor.execute("SELECT media_id FROM ai_descriptions").fetchall()
    )
    media_with_faces = set(
        r[0] for r in cursor.execute("SELECT DISTINCT media_id FROM people_faces").fetchall()
    )

    remaining = [dict(r) for r in rows if r[0] not in already_described]
    if limit is not None:
        remaining = remaining[:limit]

    if not remaining:
        conn.close()
        return {"status": "complete", "processed": 0, "already_described": len(already_described), "homework_flagged": 0}

    client = MemoryBoundedOllamaClient(
        base_url=ctx.ollama_url,
        model=ctx.ollama_model,
        recycle_interval=ctx.recycle_interval,
    )
    client.recycle_runner()

    start_time = time.time()
    batch_records: List[tuple] = []
    processed_count = 0
    homework_count = 0
    errors = 0

    try:
        for item in remaining:
            if stop_event and stop_event.is_set():
                break

            mid = item["media_id"]
            proc_path = ctx.resolve(item["processing_path"])
            if not proc_path.exists():
                errors += 1
                continue

            try:
                desc = client.describe_image(proc_path)
            except Exception:
                errors += 1
                continue

            if not desc:
                errors += 1
                continue

            has_face = mid in media_with_faces
            classified = classify_description(desc, item, has_faces=has_face)
            if classified["is_document_or_homework"]:
                homework_count += 1

            now_iso = datetime.now(timezone.utc).isoformat()
            batch_records.append(
                (
                    mid,
                    ctx.ollama_model,
                    classified["short_caption"],
                    classified["detailed_description"],
                    classified["tags"],
                    classified["scene_type"],
                    classified["primary_subjects"],
                    classified["detected_text"],
                    classified["is_document_or_homework"],
                    desc,
                    now_iso,
                )
            )
            processed_count += 1

            if len(batch_records) >= batch_size:
                _flush_vision_batch(conn, cursor, batch_records)
                batch_records.clear()

            if progress_cb and (processed_count % 5 == 0 or processed_count == len(remaining)):
                elapsed = max(0.001, time.time() - start_time)
                rate = processed_count / elapsed
                progress_cb(
                    {
                        "stage": "stage4_vision",
                        "current": processed_count,
                        "total": len(remaining),
                        "rate_img_s": round(rate, 2),
                        "homework_flagged": homework_count,
                        "message": f"Captioned {processed_count:,}/{len(remaining):,} ({rate:.2f} img/s)",
                    }
                )
    finally:
        if batch_records:
            _flush_vision_batch(conn, cursor, batch_records)
            batch_records.clear()
        client.recycle_runner()
        conn.close()

    return {
        "status": "paused" if (stop_event and stop_event.is_set()) else "complete",
        "processed": processed_count,
        "homework_flagged": homework_count,
        "errors": errors,
        "elapsed_seconds": round(time.time() - start_time, 2),
    }


def _flush_vision_batch(conn, cursor, batch_records: List[tuple]) -> None:
    cursor.executemany(
        """
        INSERT OR REPLACE INTO ai_descriptions (
            media_id, model_name, short_caption, detailed_description,
            tags, scene_type, primary_subjects, detected_text,
            is_document_or_homework, raw_response, described_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        batch_records,
    )
    conn.commit()
