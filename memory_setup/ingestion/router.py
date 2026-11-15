from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sqlite3
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from PIL import ExifTags, Image, ImageFile

from memory_setup.config import (
    ARCHIVE_ROOT_IGNORE_EXTS,
    ARCHIVE_ROOT_IGNORE_FILENAMES,
    ARCHIVE_ROOT_IGNORE_PREFIXES,
    ArchiveContext,
    IGNORE_DIRNAMES,
    IGNORE_FILENAMES,
)
from memory_setup.db import get_connection, init_db, log_operation, record_checkpoint
from memory_setup.geocoding.offline_tree import reverse_geocode
from memory_setup.ingestion.companions import group_companions
from memory_setup.ingestion.sniffer import sha256_file, sniff_file
from memory_setup.setup.checker import check_disk_space

ImageFile.LOAD_TRUNCATED_IMAGES = True

try:
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    HAS_PILLOW_HEIF = True
except Exception:
    HAS_PILLOW_HEIF = False

FILENAME_DATE_REGEX = re.compile(r"^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})")
FOLDER_YEAR_REGEX = re.compile(r"\b(19\d\d|20\d\d)\b")
EXIF_DATE_REGEX = re.compile(r"^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})")

ProgressCallback = Optional[Callable[[Dict[str, Any]], None]]


def _emit(cb: ProgressCallback, stage: str, current: int, total: int, message: str) -> None:
    if cb:
        cb({"stage": stage, "current": current, "total": total, "message": message})


def _pillow_extract_exif_basic(path: Path) -> Dict[str, Any]:
    """Pure-Python EXIF fallback when ExifTool is unavailable."""
    out: Dict[str, Any] = {}
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return out
            tag_map = {ExifTags.TAGS.get(k, str(k)): v for k, v in exif.items()}
            if "DateTimeOriginal" in tag_map:
                out["DateTimeOriginal"] = str(tag_map["DateTimeOriginal"])
            elif "DateTime" in tag_map:
                out["DateTimeOriginal"] = str(tag_map["DateTime"])
            for k in ("Make", "Model", "Software", "Orientation"):
                if k in tag_map:
                    out[k] = tag_map[k]
            # Sub-IFD Exif
            try:
                sub = exif.get_ifd(0x8769)
                for sk, sv in sub.items():
                    name = ExifTags.TAGS.get(sk, str(sk))
                    if name == "DateTimeOriginal":
                        out["DateTimeOriginal"] = str(sv)
                    elif name == "LensModel":
                        out["LensModel"] = str(sv)
                    elif name == "ISOSpeedRatings":
                        out["ISO"] = int(sv) if isinstance(sv, (int, float)) else None
                    elif name == "FNumber":
                        out["FNumber"] = float(sv)
                    elif name == "FocalLength":
                        out["FocalLength"] = float(sv)
                    elif name == "ExposureTime":
                        out["ExposureTime"] = str(sv)
            except Exception:
                pass
            # GPS IFD
            try:
                gps = exif.get_ifd(0x8825)
                if gps:
                    lat = _dms_to_deg(gps.get(2), gps.get(1))
                    lon = _dms_to_deg(gps.get(4), gps.get(3))
                    if lat is not None and lon is not None:
                        out["GPSLatitude"] = lat
                        out["GPSLongitude"] = lon
            except Exception:
                pass
    except Exception:
        pass
    return out


def _dms_to_deg(dms: Any, ref: Any) -> Optional[float]:
    if not dms or not isinstance(dms, (tuple, list)) or len(dms) < 3:
        return None
    try:
        deg = float(dms[0]) + float(dms[1]) / 60.0 + float(dms[2]) / 3600.0
        if str(ref).upper() in ("S", "W"):
            deg = -deg
        return deg
    except Exception:
        return None


def scan_sources(ctx: ArchiveContext) -> List[Dict[str, Any]]:
    """Walk source directories while strictly ignoring `content/` and `viewer/`."""
    items: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()

    for sdir in ctx.source_dirs:
        if not sdir.exists():
            continue
        for root, dirs, files in os.walk(sdir):
            dirs[:] = sorted(d for d in dirs if d.lower() not in IGNORE_DIRNAMES)
            root_path = Path(root)
            is_archive_root = root_path.resolve() == ctx.archive_root
            try:
                rel_root = root_path.relative_to(ctx.archive_root).as_posix()
            except ValueError:
                rel_root = root_path.relative_to(sdir).as_posix()

            for fname in sorted(files):
                low = fname.lower()
                if fname.startswith(".") or low in IGNORE_FILENAMES:
                    continue
                if is_archive_root and (
                    low in ARCHIVE_ROOT_IGNORE_FILENAMES
                    or any(low.startswith(prefix) for prefix in ARCHIVE_ROOT_IGNORE_PREFIXES)
                    or Path(fname).suffix.lower() in ARCHIVE_ROOT_IGNORE_EXTS
                ):
                    continue
                full_path = root_path / fname
                key = str(full_path.resolve())
                if key in seen_paths:
                    continue
                seen_paths.add(key)

                try:
                    rel_path = full_path.relative_to(ctx.archive_root).as_posix()
                except ValueError:
                    rel_path = full_path.relative_to(sdir).as_posix()

                stem, ext = os.path.splitext(fname)
                sniff = sniff_file(full_path)
                try:
                    size = full_path.stat().st_size
                except OSError:
                    size = 0

                items.append(
                    {
                        "original_full_path": str(full_path),
                        "original_rel_path": rel_path,
                        "original_filename": fname,
                        "original_stem": stem,
                        "original_ext": ext or sniff["ext"],
                        "dir_rel": rel_root,
                        "media_type": sniff["media_type"],
                        "file_size_bytes": size,
                    }
                )
    return items


def extract_timestamps_batch(items: List[Dict[str, Any]]) -> None:
    """Resolve capture timestamps using Filename -> ExifTool/Pillow -> FolderYear -> UNKNOWN_DATE."""
    unresolved_indices: List[int] = []
    for idx, item in enumerate(items):
        fn = item["original_filename"]
        m = FILENAME_DATE_REGEX.match(fn)
        if m:
            item["capture_timestamp"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}:{m.group(5)}:{m.group(6)}"
            item["capture_source"] = "Filename"
            item["year"] = m.group(1)
            item["has_unknown_date"] = 0
        else:
            unresolved_indices.append(idx)

    has_exiftool = shutil.which("exiftool") is not None
    chunk_size = 2000

    for chunk_start in range(0, len(unresolved_indices), chunk_size):
        chunk_idx = unresolved_indices[chunk_start : chunk_start + chunk_size]
        file_paths = [items[i]["original_full_path"] for i in chunk_idx]
        exif_by_path: Dict[str, Dict[str, Any]] = {}

        if has_exiftool and file_paths:
            try:
                proc = subprocess.Popen(
                    [
                        "exiftool",
                        "-json",
                        "-fast2",
                        "-DateTimeOriginal",
                        "-CreateDate",
                        "-CreationDate",
                        "-ContentIdentifier",
                        "-MediaGroupUUID",
                        "-@",
                        "-",
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                stdout, _ = proc.communicate(input="\n".join(file_paths), timeout=120)
                if stdout.strip():
                    for entry in json.loads(stdout):
                        if "SourceFile" in entry:
                            exif_by_path[entry["SourceFile"]] = entry
            except Exception:
                exif_by_path = {}

        for i in chunk_idx:
            item = items[i]
            orig_p = item["original_full_path"]
            meta = exif_by_path.get(orig_p)
            if meta is None and item["media_type"] == "photo":
                meta = _pillow_extract_exif_basic(Path(orig_p))
            meta = meta or {}

            cid = meta.get("ContentIdentifier") or meta.get("MediaGroupUUID")
            if cid:
                item["content_identifier"] = str(cid)

            raw_dt = meta.get("DateTimeOriginal") or meta.get("CreateDate") or meta.get("CreationDate")
            found_dt = None
            source = "None"
            year = None

            if raw_dt and isinstance(raw_dt, str):
                m_exif = EXIF_DATE_REGEX.match(raw_dt)
                if m_exif and m_exif.group(1) != "0000":
                    found_dt = f"{m_exif.group(1)}-{m_exif.group(2)}-{m_exif.group(3)}T{m_exif.group(4)}:{m_exif.group(5)}:{m_exif.group(6)}"
                    source = "EXIF"
                    year = m_exif.group(1)

            if not found_dt:
                m_fold = FOLDER_YEAR_REGEX.search(item["dir_rel"])
                if m_fold:
                    year = m_fold.group(1)
                    found_dt = f"{year}-01-01T00:00:00"
                    source = "FolderYearFallback"

            if found_dt and year:
                item["capture_timestamp"] = found_dt
                item["capture_source"] = source
                item["year"] = year
                item["has_unknown_date"] = 0 if source == "EXIF" else 1
            else:
                item["capture_timestamp"] = None
                item["capture_source"] = "None"
                item["year"] = "UNKNOWN_DATE"
                item["has_unknown_date"] = 1


def _next_id_counters(conn: sqlite3.Connection) -> Dict[str, int]:
    counters = {"P": 1, "V": 1, "S": 1, "X": 1, "GRP": 1}
    cur = conn.cursor()
    for prefix in ("P", "V", "S", "X"):
        max_num = 0
        for (val,) in cur.execute("SELECT id FROM media_items WHERE id LIKE ?", (f"{prefix}_%",)).fetchall():
            if val and "_" in val:
                try:
                    num = int(val.split("_")[1])
                    if num > max_num:
                        max_num = num
                except ValueError:
                    pass
        counters[prefix] = max_num + 1
    max_grp = 0
    for (gval,) in cur.execute(
        "SELECT companion_group_id FROM media_items WHERE companion_group_id LIKE 'GRP_%'"
    ).fetchall():
        if gval and "_" in gval:
            try:
                num = int(gval.split("_")[1])
                if num > max_grp:
                    max_grp = num
            except ValueError:
                pass
    counters["GRP"] = max_grp + 1
    return counters


def assign_permanent_ids_and_targets(ctx: ArchiveContext, items: List[Dict[str, Any]], counters: Dict[str, int]) -> None:
    """Assign deterministic continuous IDs and resolve strict two-folder destination paths."""
    items.sort(key=lambda x: x["original_rel_path"])

    for item in items:
        mtype = item["media_type"]
        if mtype == "photo":
            item["id"] = f"P_{counters['P']:06d}"
            counters["P"] += 1
        elif mtype == "video":
            item["id"] = f"V_{counters['V']:06d}"
            counters["V"] += 1
        elif mtype in ("sidecar", "audio_memo"):
            item["id"] = f"S_{counters['S']:06d}"
            counters["S"] += 1
        else:
            item["id"] = f"X_{counters['X']:06d}"
            counters["X"] += 1

    # Sync companion timestamps with primary photo
    group_map: Dict[str, List[int]] = defaultdict(list)
    for idx, item in enumerate(items):
        gid = item.get("companion_group_id")
        if gid:
            group_map[gid].append(idx)

    for indices in group_map.values():
        ts_primary = None
        yr_primary = None
        for i in indices:
            if items[i].get("capture_timestamp") and not items[i]["has_unknown_date"]:
                ts_primary = items[i]["capture_timestamp"]
                yr_primary = items[i]["year"]
                break
        if ts_primary and yr_primary:
            for i in indices:
                items[i]["capture_timestamp"] = ts_primary
                items[i]["year"] = yr_primary
                items[i]["has_unknown_date"] = 0

    for item in items:
        mtype = item["media_type"]
        ts = item.get("capture_timestamp")
        year = item.get("year", "UNKNOWN_DATE")
        pid = item["id"]
        ext = item["original_ext"]

        if ts and not item["has_unknown_date"]:
            ts_clean = ts.replace("-", "").replace(":", "").replace("T", "_")
            target_fn = f"{ts_clean}__{pid}{ext}"
        else:
            target_fn = f"UNKNOWN_DATE__{pid}{ext}"

        if mtype == "photo":
            target_sub = ctx.target_images_dir / year
        elif mtype == "video":
            target_sub = ctx.target_videos_dir / year
        elif mtype == "sidecar":
            if item.get("companion_role") == "live_photo_video":
                target_sub = ctx.target_videos_dir / year
            else:
                target_sub = ctx.target_images_dir / year
        elif mtype == "audio_memo":
            target_sub = ctx.target_images_dir / year
        elif mtype == "document":
            target_sub = ctx.documents_dir
            target_fn = f"{pid}__{item['original_filename']}"
        else:
            target_sub = ctx.documents_dir / "other"
            target_fn = f"{pid}__{item['original_filename']}"

        target_path = target_sub / target_fn
        item["current_full_path"] = str(target_path)
        item["current_rel_path"] = target_path.relative_to(ctx.archive_root).as_posix()
        item["current_filename"] = target_fn


def _backfill_candidate_original_hashes(
    ctx: ArchiveContext,
    conn: sqlite3.Connection,
    candidate_sizes: set[int],
) -> None:
    """Populate missing `sha256_original` values for existing rows matching incoming sizes."""
    conn.execute(
        """
        UPDATE media_items
        SET sha256_original = (
            SELECT p.sha256_processing
            FROM processing_inputs p
            WHERE p.media_id = media_items.id
              AND p.is_reused_original = 1
              AND p.sha256_processing IS NOT NULL
              AND p.sha256_processing != ''
        )
        WHERE sha256_original IS NULL
          AND EXISTS (
            SELECT 1
            FROM processing_inputs p
            WHERE p.media_id = media_items.id
              AND p.is_reused_original = 1
              AND p.sha256_processing IS NOT NULL
              AND p.sha256_processing != ''
        )
        """
    )
    if candidate_sizes:
        missing_rows = conn.execute(
            "SELECT id, current_path, file_size_bytes FROM media_items WHERE sha256_original IS NULL"
        ).fetchall()
        updates: List[Tuple[str, str]] = []
        for mid, cur_path, fsize in missing_rows:
            if fsize not in candidate_sizes:
                continue
            resolved = ctx.resolve(cur_path)
            if resolved.is_file():
                try:
                    updates.append((sha256_file(resolved), mid))
                except OSError:
                    pass
        if updates:
            conn.executemany("UPDATE media_items SET sha256_original = ? WHERE id = ?", updates)
    conn.commit()


def _ensure_unique_target(dst: Path, item: Dict[str, Any], ctx: ArchiveContext) -> Path:
    """Return a non-colliding target path inside `content/` and keep item fields in sync."""
    if not dst.exists():
        return dst
    stem = dst.stem
    suffix = dst.suffix
    counter = 1
    candidate = dst.with_name(f"{stem}_{counter}{suffix}")
    while candidate.exists():
        counter += 1
        candidate = dst.with_name(f"{stem}_{counter}{suffix}")
    item["current_full_path"] = str(candidate)
    item["current_filename"] = candidate.name
    item["current_rel_path"] = candidate.relative_to(ctx.archive_root).as_posix()
    return candidate


def run_stage1_consolidation(
    ctx: ArchiveContext,
    deduplicate: bool = True,
    progress_cb: ProgressCallback = None,
) -> Dict[str, Any]:
    """Execute Stage 1: Scan, deduplicate, pair companions, and consolidate into `content/`."""
    free_gb = check_disk_space(ctx.archive_root)
    if free_gb < ctx.min_free_disk_gb:
        raise RuntimeError(
            f"Free disk space ({free_gb:.2f} GiB) is below safety floor ({ctx.min_free_disk_gb} GiB)."
        )

    ctx.ensure_layout()
    init_db(ctx.db_path)

    _emit(progress_cb, "stage1", 0, 1, "Scanning source directories...")
    items = scan_sources(ctx)
    if not items:
        return {"total_scanned": 0, "success_count": 0, "failure_count": 0, "duplicates_skipped": 0}

    conn = get_connection(ctx.db_path)
    counters = _next_id_counters(conn)

    if deduplicate:
        candidate_sizes = {int(it["file_size_bytes"]) for it in items}
        _backfill_candidate_original_hashes(ctx, conn, candidate_sizes)

    # Deduplicate against existing catalog SHA-256 or within the scan batch
    existing_hashes = set(
        r[0]
        for r in conn.execute("SELECT sha256_original FROM media_items WHERE sha256_original IS NOT NULL").fetchall()
    )
    duplicates_skipped = 0
    unique_items: List[Dict[str, Any]] = []
    for idx, item in enumerate(items):
        src_path = Path(item["original_full_path"])
        try:
            digest = sha256_file(src_path)
        except OSError:
            digest = None
        item["sha256_original"] = digest
        if deduplicate and digest and digest in existing_hashes:
            duplicates_skipped += 1
            continue
        if digest:
            existing_hashes.add(digest)
        unique_items.append(item)

    items = unique_items
    if not items:
        conn.close()
        return {
            "total_scanned": 0,
            "success_count": 0,
            "failure_count": 0,
            "duplicates_skipped": duplicates_skipped,
        }

    extract_timestamps_batch(items)
    group_companions(items, start_seq=counters["GRP"])
    assign_permanent_ids_and_targets(ctx, items, counters)

    action_name = "MOVE_AND_RENAME" if ctx.mode == "in_place" else "SAFE_COPY"
    success_count = 0
    failure_count = 0
    errors: List[str] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()

    for idx, item in enumerate(items, 1):
        src = Path(item["original_full_path"])
        dst = _ensure_unique_target(Path(item["current_full_path"]), item, ctx)
        if not src.exists():
            failure_count += 1
            errors.append(f"Source missing: {src}")
            continue

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if ctx.mode == "in_place":
                try:
                    os.rename(src, dst)
                except OSError:
                    shutil.copy2(src, dst)
                    src.unlink()
            else:
                shutil.copy2(src, dst)

            cursor.execute(
                """
                INSERT INTO media_items (
                    id, media_type, original_rel_path, original_filename, original_ext,
                    current_path, current_filename, file_size_bytes, sha256_original,
                    companion_group_id, companion_role, has_unknown_date, capture_timestamp,
                    capture_source, processing_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["media_type"],
                    item["original_rel_path"],
                    item["original_filename"],
                    item["original_ext"],
                    item["current_rel_path"],
                    item["current_filename"],
                    item["file_size_bytes"],
                    item.get("sha256_original"),
                    item.get("companion_group_id"),
                    item.get("companion_role", "none"),
                    item["has_unknown_date"],
                    item.get("capture_timestamp"),
                    item.get("capture_source", "none"),
                    "stage1_consolidated",
                    now_iso,
                    now_iso,
                ),
            )
            log_operation(
                conn,
                item["id"],
                "stage1_consolidation",
                action_name,
                item["original_rel_path"],
                item["current_rel_path"],
                "SUCCESS",
                {"mode": ctx.mode, "year": item.get("year")},
            )
            success_count += 1
        except Exception as exc:
            failure_count += 1
            errors.append(f"{src} -> {dst}: {exc}")
            log_operation(
                conn,
                item["id"],
                "stage1_consolidation",
                action_name,
                item["original_rel_path"],
                item["current_rel_path"],
                "FAILED",
                {"error": str(exc)},
            )

        if idx % 500 == 0 or idx == len(items):
            conn.commit()
            _emit(progress_cb, "stage1", idx, len(items), f"Consolidated {idx:,}/{len(items):,} files")

    # Prune empty directories when running in-place
    if ctx.mode == "in_place":
        for sdir in ctx.source_dirs:
            for root, dirs, _ in os.walk(sdir, topdown=False):
                rp = Path(root)
                if rp == sdir or rp == ctx.archive_root:
                    continue
                if any(part.lower() in IGNORE_DIRNAMES for part in rp.parts):
                    continue
                try:
                    rp.rmdir()
                except OSError:
                    pass

    type_counts = Counter(item["media_type"] for item in items)
    report_json_path = ctx.reports_dir / "stage1_report.json"
    summary = {
        "stage": "stage1_consolidation",
        "mode": ctx.mode,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "total_scanned": len(items),
        "success_count": success_count,
        "failure_count": failure_count,
        "duplicates_skipped": duplicates_skipped,
        "media_type_breakdown": dict(type_counts),
        "free_disk_gb_remaining": round(check_disk_space(ctx.archive_root), 2),
        "errors": errors[:30],
    }
    report_json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    record_checkpoint(conn, "stage1_consolidation", len(items), success_count, failure_count, str(report_json_path), summary)
    conn.commit()
    conn.close()
    return summary


def _convert_single_proxy(task: Tuple[str, str, str, str]) -> Tuple[str, str, bool, Optional[str]]:
    """Convert non-browser image (HEIC/WebP/TIFF/BMP) to <=1600px JPEG proxy with fidelity check."""
    media_id, archive_root_str, src_rel, dst_rel = task
    archive_root = Path(archive_root_str)
    src_full = archive_root / src_rel
    dst_full = archive_root / dst_rel
    dst_full.parent.mkdir(parents=True, exist_ok=True)

    # 1. Try macOS sips (fast hardware-accelerated HEIC decode)
    if shutil.which("sips") is not None:
        res = subprocess.run(
            ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85", "-Z", "1600", str(src_full), "--out", str(dst_full)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if res.returncode == 0 and _verify_non_black_image(dst_full):
            return media_id, dst_rel, True, None

    # 2. Pure-Python Pillow / pillow-heif fallback (cross-platform macOS + Windows + Linux)
    try:
        with Image.open(src_full) as img:
            rgb = img.convert("RGB")
            rgb.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            rgb.save(dst_full, "JPEG", quality=85)
        if _verify_non_black_image(dst_full):
            return media_id, dst_rel, True, None
    except Exception as exc:
        pillow_err = str(exc)
    else:
        pillow_err = "Generated proxy was empty or all-black"

    # 3. ImageMagick fallback
    if shutil.which("magick") is not None:
        res2 = subprocess.run(
            ["magick", str(src_full), "-resize", "1600x1600>", "-quality", "85", str(dst_full)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if res2.returncode == 0 and _verify_non_black_image(dst_full):
            return media_id, dst_rel, True, None

    return media_id, dst_rel, False, f"Proxy conversion failed for {src_rel}: {pillow_err}"


def _verify_non_black_image(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        with Image.open(path) as img:
            arr = np.asarray(img)
            return bool(arr.size > 0 and arr.max() > 0)
    except Exception:
        return False


def run_stage2_standardization(
    ctx: ArchiveContext,
    max_workers: int = 8,
    progress_cb: ProgressCallback = None,
    reprocess: Optional[bool] = None,
) -> Dict[str, Any]:
    """Execute Stage 2: Batch EXIF/GPS extraction, offline geocoding, and proxy generation."""
    free_gb = check_disk_space(ctx.archive_root)
    if free_gb < ctx.min_free_disk_gb:
        raise RuntimeError(f"Free disk space ({free_gb:.2f} GiB) below threshold ({ctx.min_free_disk_gb} GiB).")

    do_reprocess = ctx.reprocess if reprocess is None else reprocess
    conn = get_connection(ctx.db_path)
    cursor = conn.cursor()
    total_photos = int(
        cursor.execute("SELECT COUNT(*) FROM media_items WHERE media_type = 'photo'").fetchone()[0] or 0
    )

    if total_photos == 0:
        conn.close()
        return {"total_photos": 0, "metadata_extracted": 0, "photos_with_gps": 0, "processing_inputs_ready": 0}

    if do_reprocess:
        photos_for_exif = [
            dict(r)
            for r in cursor.execute(
                "SELECT id, current_path, original_ext, file_size_bytes FROM media_items WHERE media_type = 'photo'"
            ).fetchall()
        ]
    else:
        photos_for_exif = [
            dict(r)
            for r in cursor.execute(
                """
                SELECT m.id, m.current_path, m.original_ext, m.file_size_bytes
                FROM media_items m
                LEFT JOIN metadata_exif e ON m.id = e.media_id
                WHERE m.media_type = 'photo' AND e.media_id IS NULL
                """
            ).fetchall()
        ]

    has_exiftool = shutil.which("exiftool") is not None
    chunk_size = 2000
    meta_count = 0
    gps_count = 0

    for start_idx in range(0, len(photos_for_exif), chunk_size):
        chunk = photos_for_exif[start_idx : start_idx + chunk_size]
        path_to_id = {str(ctx.resolve(p["current_path"])): p["id"] for p in chunk}
        exif_by_path: Dict[str, Dict[str, Any]] = {}

        if has_exiftool:
            try:
                proc = subprocess.Popen(
                    [
                        "exiftool",
                        "-json",
                        "-fast2",
                        "-n",
                        "-Make",
                        "-Model",
                        "-LensModel",
                        "-Software",
                        "-DateTimeOriginal",
                        "-FileModifyDate",
                        "-GPSLatitude",
                        "-GPSLongitude",
                        "-GPSAltitude",
                        "-Orientation",
                        "-ISO",
                        "-ExposureTime",
                        "-FNumber",
                        "-FocalLength",
                        "-@",
                        "-",
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                stdout, _ = proc.communicate(input="\n".join(path_to_id.keys()), timeout=120)
                if stdout.strip():
                    for rec in json.loads(stdout):
                        if "SourceFile" in rec:
                            exif_by_path[rec["SourceFile"]] = rec
            except Exception:
                exif_by_path = {}

        for p in chunk:
            mid = p["id"]
            full_p = ctx.resolve(p["current_path"])
            rec = exif_by_path.get(str(full_p))
            if rec is None:
                rec = _pillow_extract_exif_basic(full_p)
            lat = rec.get("GPSLatitude")
            lon = rec.get("GPSLongitude")
            alt = rec.get("GPSAltitude")

            if lat is not None and lon is not None:
                city, state, country = reverse_geocode(float(lat), float(lon))
                gps_count += 1
            else:
                lat = lon = alt = None
                city = state = country = None

            cursor.execute(
                """
                INSERT OR REPLACE INTO metadata_exif (
                    media_id, make, model, lens_model, software, date_time_original,
                    file_modify_date, gps_latitude, gps_longitude, gps_altitude,
                    city, state, country, orientation, iso, exposure_time, f_number,
                    focal_length, raw_exif_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mid,
                    rec.get("Make"),
                    rec.get("Model"),
                    rec.get("LensModel"),
                    rec.get("Software"),
                    rec.get("DateTimeOriginal"),
                    rec.get("FileModifyDate"),
                    lat,
                    lon,
                    alt,
                    city,
                    state,
                    country,
                    rec.get("Orientation"),
                    rec.get("ISO"),
                    str(rec.get("ExposureTime")) if rec.get("ExposureTime") is not None else None,
                    rec.get("FNumber"),
                    rec.get("FocalLength"),
                    json.dumps(rec),
                ),
            )
            meta_count += 1

        conn.commit()
        _emit(
            progress_cb,
            "stage2",
            min(start_idx + chunk_size, len(photos_for_exif)),
            len(photos_for_exif),
            f"Extracted EXIF metadata for {meta_count:,}/{len(photos_for_exif):,} photos",
        )

    # Processing inputs: reuse JPEG/PNG, generate verified proxies in viewer/.processing_cache/
    ctx.processing_cache_dir.mkdir(parents=True, exist_ok=True)
    input_rows = cursor.execute(
        """
        SELECT m.id, m.current_path, m.original_ext, m.file_size_bytes,
               p.media_id AS has_input, p.processing_path
        FROM media_items m
        LEFT JOIN processing_inputs p ON m.id = p.media_id
        WHERE m.media_type = 'photo'
        """
    ).fetchall()

    compatible: List[Dict[str, Any]] = []
    reused_cached_proxies: List[Tuple[str, str]] = []
    conversion_tasks: List[Tuple[str, str, str, str]] = []
    touched_ids: List[str] = []

    for r in input_rows:
        p = dict(r)
        has_input = p["has_input"] is not None
        if not do_reprocess and has_input and p["processing_path"] and ctx.resolve(p["processing_path"]).exists():
            continue

        ext = p["original_ext"].lower()
        touched_ids.append(p["id"])
        if ext in (".jpg", ".jpeg", ".png"):
            compatible.append(p)
        else:
            dst_rel = f"viewer/.processing_cache/{p['id']}.jpg"
            dst_full = ctx.resolve(dst_rel)
            if not do_reprocess and _verify_non_black_image(dst_full):
                reused_cached_proxies.append((p["id"], dst_rel))
            else:
                conversion_tasks.append((p["id"], str(ctx.archive_root), p["current_path"], dst_rel))

    for p in compatible:
        fmt = "PNG" if p["original_ext"].lower() == ".png" else "JPEG"
        cursor.execute(
            """
            INSERT OR REPLACE INTO processing_inputs (
                media_id, processing_path, is_reused_original, format,
                pixel_width, pixel_height, aspect_ratio, megapixels,
                color_mode, sha256_processing, validation_status, validation_error
            ) VALUES (?, ?, 1, ?, 0, 0, 0.0, 0.0, 'UNKNOWN', '', 'REGISTERED', NULL)
            """,
            (p["id"], p["current_path"], fmt),
        )
    for mid, dst_rel in reused_cached_proxies:
        cursor.execute(
            """
            INSERT OR REPLACE INTO processing_inputs (
                media_id, processing_path, is_reused_original, format,
                pixel_width, pixel_height, aspect_ratio, megapixels,
                color_mode, sha256_processing, validation_status, validation_error
            ) VALUES (?, ?, 0, 'JPEG', 0, 0, 0.0, 0.0, 'UNKNOWN', '', 'REGISTERED', NULL)
            """,
            (mid, dst_rel),
        )
    conn.commit()

    success_conv = len(reused_cached_proxies)
    failed_conv = 0
    errors: List[str] = []

    if conversion_tasks:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_convert_single_proxy, t) for t in conversion_tasks]
            for idx, fut in enumerate(as_completed(futures), 1):
                mid, dst_rel, ok, err = fut.result()
                if ok:
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO processing_inputs (
                            media_id, processing_path, is_reused_original, format,
                            pixel_width, pixel_height, aspect_ratio, megapixels,
                            color_mode, sha256_processing, validation_status, validation_error
                        ) VALUES (?, ?, 0, 'JPEG', 0, 0, 0.0, 0.0, 'UNKNOWN', '', 'REGISTERED', NULL)
                        """,
                        (mid, dst_rel),
                    )
                    success_conv += 1
                else:
                    failed_conv += 1
                    if err:
                        errors.append(err)

                if idx % 200 == 0 or idx == len(conversion_tasks):
                    conn.commit()
                    cur_free = check_disk_space(ctx.archive_root)
                    if cur_free < ctx.min_free_disk_gb:
                        raise RuntimeError(f"ABORTING: Free disk dropped to {cur_free:.2f} GiB!")
                    _emit(
                        progress_cb,
                        "stage2",
                        idx,
                        len(conversion_tasks),
                        f"Generated {idx:,}/{len(conversion_tasks):,} proxies",
                    )

    if do_reprocess:
        cursor.execute("UPDATE media_items SET processing_status = 'stage2_standardized' WHERE media_type = 'photo'")
    else:
        cursor.execute(
            "UPDATE media_items SET processing_status = 'stage2_standardized' WHERE media_type = 'photo' AND processing_status = 'stage1_consolidated'"
        )
        if touched_ids:
            cursor.executemany(
                "UPDATE media_items SET processing_status = 'stage2_standardized' WHERE id = ?",
                [(mid,) for mid in touched_ids],
            )

    total_inputs_ready = int(cursor.execute("SELECT COUNT(*) FROM processing_inputs").fetchone()[0] or 0)
    total_gps = int(
        cursor.execute(
            "SELECT COUNT(*) FROM metadata_exif WHERE gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL"
        ).fetchone()[0]
        or 0
    )
    summary = {
        "stage": "stage2_metadata_and_standardization",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "total_photos": total_photos,
        "metadata_extracted": meta_count,
        "photos_with_gps": total_gps,
        "processing_inputs_ready": total_inputs_ready,
        "newly_processed_inputs": len(compatible) + success_conv,
        "failed_conversions": failed_conv,
        "errors": errors[:30],
    }
    report_path = ctx.reports_dir / "stage2_report.json"
    report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    record_checkpoint(conn, "stage2_standardization", total_photos, total_inputs_ready, failed_conv, str(report_path), summary)
    conn.commit()
    conn.close()
    return summary


def compute_dhash(image: Image.Image, hash_size: int = 8) -> str:
    gray = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.BILINEAR)
    raw = gray.tobytes()
    decimal_val = 0
    idx = 0
    for row in range(hash_size):
        row_offset = row * (hash_size + 1)
        for col in range(hash_size):
            if raw[row_offset + col] > raw[row_offset + col + 1]:
                decimal_val |= 1 << idx
            idx += 1
    return f"{decimal_val:016x}"


def hamming_dist(h1: str, h2: str) -> int:
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count("1")
    except Exception:
        return 999


def _validate_and_hash(task: Tuple[str, str]) -> Dict[str, Any]:
    media_id, full_path_str = task
    full_path = Path(full_path_str)
    if not full_path.exists():
        return {"media_id": media_id, "status": "MISSING", "error": f"File not found: {full_path}"}
    try:
        digest = sha256_file(full_path)
        with Image.open(full_path) as img:
            img.verify()
        with Image.open(full_path) as img:
            img.load()
            w, h = img.size
            mode = img.mode
            fmt = img.format or "JPEG"
            dh = compute_dhash(img)
        return {
            "media_id": media_id,
            "status": "VALID",
            "error": None,
            "sha256": digest,
            "width": w,
            "height": h,
            "aspect_ratio": round(w / h, 4) if h > 0 else 0.0,
            "megapixels": round((w * h) / 1_000_000.0, 2),
            "color_mode": mode,
            "format": fmt,
            "dhash": dh,
        }
    except Exception as exc:
        return {"media_id": media_id, "status": "CORRUPT", "error": str(exc)}


def run_stage3_validation(
    ctx: ArchiveContext,
    max_workers: int = 8,
    progress_cb: ProgressCallback = None,
    reprocess: Optional[bool] = None,
) -> Dict[str, Any]:
    """Execute Stage 3: Byte/raster validation, dHash computation, and canonical AI manifest export."""
    do_reprocess = ctx.reprocess if reprocess is None else reprocess
    conn = get_connection(ctx.db_path)
    cursor = conn.cursor()

    rows = cursor.execute(
        """
        SELECT p.media_id, p.processing_path, p.format, p.pixel_width, p.pixel_height,
               p.aspect_ratio, p.megapixels, p.color_mode, p.sha256_processing,
               p.dhash, p.validation_status,
               m.current_path, m.original_rel_path, m.capture_timestamp, m.has_unknown_date,
               e.make, e.model, e.gps_latitude, e.gps_longitude, e.city, e.country
        FROM processing_inputs p
        JOIN media_items m ON p.media_id = m.id
        LEFT JOIN metadata_exif e ON p.media_id = e.media_id
        """
    ).fetchall()
    all_items = [dict(r) for r in rows]

    tasks: List[Tuple[str, str]] = []
    validated: Dict[str, Dict[str, Any]] = {}
    newly_validated: List[Dict[str, Any]] = []
    valid_count = 0
    corrupt_count = 0
    errors: List[Tuple[str, str]] = []

    for item in all_items:
        mid = item["media_id"]
        if (
            not do_reprocess
            and item["validation_status"] == "VALID"
            and bool(item.get("dhash"))
            and bool(item.get("sha256_processing"))
        ):
            validated[mid] = {
                "media_id": mid,
                "status": "VALID",
                "width": item["pixel_width"],
                "height": item["pixel_height"],
                "aspect_ratio": item["aspect_ratio"],
                "megapixels": item["megapixels"],
                "color_mode": item["color_mode"],
                "format": item["format"],
                "sha256": item["sha256_processing"],
                "dhash": item["dhash"],
            }
            valid_count += 1
        else:
            tasks.append((mid, str(ctx.resolve(item["processing_path"]))))

    if tasks:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_validate_and_hash, t) for t in tasks]
            for idx, fut in enumerate(as_completed(futures), 1):
                res = fut.result()
                mid = res["media_id"]
                validated[mid] = res
                newly_validated.append(res)
                if res["status"] == "VALID":
                    valid_count += 1
                else:
                    corrupt_count += 1
                    errors.append((mid, res["error"] or "Unknown error"))
                if idx % 500 == 0 or idx == len(tasks):
                    _emit(progress_cb, "stage3", idx, len(tasks), f"Validated {idx:,}/{len(tasks):,} images")

    update_rows = []
    for res in newly_validated:
        mid = res["media_id"]
        if res["status"] == "VALID":
            update_rows.append(
                (
                    res["width"],
                    res["height"],
                    res["aspect_ratio"],
                    res["megapixels"],
                    res["color_mode"],
                    res["sha256"],
                    res["dhash"],
                    res["status"],
                    None,
                    mid,
                )
            )
        else:
            update_rows.append((0, 0, 0.0, 0.0, "UNKNOWN", "", "", res["status"], res["error"], mid))

    if update_rows:
        cursor.executemany(
            """
            UPDATE processing_inputs
            SET pixel_width = ?, pixel_height = ?, aspect_ratio = ?, megapixels = ?,
                color_mode = ?, sha256_processing = ?, dhash = ?,
                validation_status = ?, validation_error = ?
            WHERE media_id = ?
            """,
            update_rows,
        )
        conn.commit()

    # Match UNKNOWN_DATE items against dated archive using dHash
    unknown_items = [
        it for it in all_items if it["has_unknown_date"] == 1 and validated.get(it["media_id"], {}).get("status") == "VALID"
    ]
    dated_items = [
        it for it in all_items if it["has_unknown_date"] == 0 and validated.get(it["media_id"], {}).get("status") == "VALID"
    ]
    dated_hashes = [
        (d["media_id"], validated[d["media_id"]]["dhash"], d["capture_timestamp"], d["current_path"])
        for d in dated_items
        if validated[d["media_id"]].get("dhash")
    ]

    matches_found: List[Dict[str, Any]] = []
    for u in unknown_items:
        u_mid = u["media_id"]
        u_h = validated[u_mid].get("dhash")
        if not u_h:
            continue
        best_match = None
        min_dist = 999
        for d_mid, d_h, d_ts, d_path in dated_hashes:
            dist = hamming_dist(u_h, d_h)
            if dist < min_dist:
                min_dist = dist
                best_match = (d_mid, d_ts, d_path)
                if dist == 0:
                    break
        if best_match and min_dist <= 4:
            matches_found.append(
                {
                    "unknown_id": u_mid,
                    "matched_id": best_match[0],
                    "matched_timestamp": best_match[1],
                    "hamming_distance": min_dist,
                }
            )

    match_dict = {m["unknown_id"]: m for m in matches_found}

    cursor.execute("DROP TABLE IF EXISTS ai_manifest")
    cursor.execute(
        """
        CREATE TABLE ai_manifest (
            id TEXT PRIMARY KEY,
            original_path TEXT NOT NULL,
            current_path TEXT NOT NULL,
            processing_path TEXT NOT NULL,
            format TEXT NOT NULL,
            pixel_width INTEGER NOT NULL,
            pixel_height INTEGER NOT NULL,
            aspect_ratio REAL NOT NULL,
            megapixels REAL NOT NULL,
            color_mode TEXT NOT NULL,
            sha256_processing TEXT NOT NULL,
            dhash TEXT,
            capture_timestamp TEXT,
            has_unknown_date INTEGER NOT NULL,
            camera_make TEXT,
            camera_model TEXT,
            gps_latitude REAL,
            gps_longitude REAL,
            city TEXT,
            country TEXT,
            matched_dated_id TEXT,
            matched_timestamp TEXT
        )
        """
    )

    manifest_rows = []
    for item in all_items:
        mid = item["media_id"]
        v = validated.get(mid, {})
        if v.get("status") != "VALID":
            continue
        m_info = match_dict.get(mid)
        manifest_rows.append(
            (
                mid,
                item["original_rel_path"],
                item["current_path"],
                item["processing_path"],
                v["format"],
                v["width"],
                v["height"],
                v["aspect_ratio"],
                v["megapixels"],
                v["color_mode"],
                v["sha256"],
                v["dhash"],
                item.get("capture_timestamp"),
                item["has_unknown_date"],
                item.get("make"),
                item.get("model"),
                item.get("gps_latitude"),
                item.get("gps_longitude"),
                item.get("city"),
                item.get("country"),
                m_info["matched_id"] if m_info else None,
                m_info["matched_timestamp"] if m_info else None,
            )
        )

    cursor.executemany(
        "INSERT INTO ai_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        manifest_rows,
    )
    if do_reprocess:
        cursor.execute("UPDATE media_items SET processing_status = 'stage3_validated' WHERE media_type = 'photo'")
    else:
        cursor.execute(
            """
            UPDATE media_items
            SET processing_status = 'stage3_validated'
            WHERE media_type = 'photo'
              AND processing_status IN ('stage1_consolidated', 'stage2_standardized')
            """
        )
        if newly_validated:
            cursor.executemany(
                "UPDATE media_items SET processing_status = 'stage3_validated' WHERE id = ?",
                [(res["media_id"],) for res in newly_validated],
            )
    conn.commit()

    keys = [
        "id",
        "original_path",
        "current_path",
        "processing_path",
        "format",
        "pixel_width",
        "pixel_height",
        "aspect_ratio",
        "megapixels",
        "color_mode",
        "sha256_processing",
        "dhash",
        "capture_timestamp",
        "has_unknown_date",
        "camera_make",
        "camera_model",
        "gps_latitude",
        "gps_longitude",
        "city",
        "country",
        "matched_dated_id",
        "matched_timestamp",
    ]
    manifest_dicts = [dict(zip(keys, row)) for row in manifest_rows]
    ctx.manifest_json_path.write_text(json.dumps(manifest_dicts, indent=2), encoding="utf-8")
    with open(ctx.manifest_csv_path, "w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=keys)
        writer.writeheader()
        writer.writerows(manifest_dicts)

    summary = {
        "stage": "stage3_validation_and_ai_manifest",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "total_images_evaluated": len(all_items),
        "valid_images": valid_count,
        "corrupt_images": corrupt_count,
        "unknown_date_matches_found": len(matches_found),
        "manifest_entries": len(manifest_rows),
        "errors": errors[:20],
    }
    report_path = ctx.reports_dir / "stage3_report.json"
    report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    record_checkpoint(conn, "stage3_validation", len(all_items), valid_count, corrupt_count, str(report_path), summary)
    conn.commit()
    conn.close()
    return summary
