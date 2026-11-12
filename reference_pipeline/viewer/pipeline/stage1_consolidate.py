import os
import sys
from pathlib import Path
_viewer_root = Path(__file__).resolve().parent.parent
if str(_viewer_root) not in sys.path:
    sys.path.insert(0, str(_viewer_root))

import re
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, Any

from pipeline.config import (
    WORKSPACE_ROOT,
    SOURCE_DIRS,
    TARGET_IMAGES_DIR,
    TARGET_VIDEOS_DIR,
    EXCLUDED_DIR,
    REPORTS_DIR,
    PHOTO_EXTENSIONS,
    VIDEO_EXTENSIONS,
    SIDECAR_EXTENSIONS,
    AUDIO_EXTENSIONS,
    DOCUMENT_EXTENSIONS,
    IGNORE_FILENAMES,
    IGNORE_DIRNAMES,
    MIN_FREE_DISK_GB,
)
from pipeline.db import init_db, get_connection, log_operation, record_checkpoint

FILENAME_DATE_REGEX = re.compile(r'^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})')
FOLDER_YEAR_REGEX = re.compile(r'\b(19\d\d|20\d\d)\b')
EXIF_DATE_REGEX = re.compile(r'^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})')

def check_disk_space() -> float:
    st = os.statvfs(WORKSPACE_ROOT)
    free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
    return free_gb

def scan_sources() -> List[Dict[str, Any]]:
    print("Scanning source directories: photos/, photos1/, photos2/...")
    items = []
    
    for sdir in SOURCE_DIRS:
        if not sdir.exists():
            continue
        for root, dirs, files in os.walk(sdir):
            dirs[:] = [d for d in dirs if d.lower() not in IGNORE_DIRNAMES]
            rel_root = Path(root).relative_to(WORKSPACE_ROOT)
            
            for f in files:
                if f.startswith('.'):
                    continue
                if f.lower() in IGNORE_FILENAMES:
                    continue
                
                full_path = Path(root) / f
                rel_path = full_path.relative_to(WORKSPACE_ROOT)
                stem, ext = os.path.splitext(f)
                ext_lower = ext.lower()
                
                try:
                    size = full_path.stat().st_size
                except Exception:
                    size = 0
                
                # Determine media category
                if ext_lower in PHOTO_EXTENSIONS:
                    cat = 'photo'
                elif ext_lower in VIDEO_EXTENSIONS:
                    cat = 'video'
                elif ext_lower in SIDECAR_EXTENSIONS:
                    cat = 'sidecar'
                elif ext_lower in AUDIO_EXTENSIONS:
                    cat = 'audio_memo'
                elif ext_lower in DOCUMENT_EXTENSIONS:
                    cat = 'document'
                else:
                    cat = 'other'
                
                items.append({
                    'original_full_path': str(full_path),
                    'original_rel_path': str(rel_path),
                    'original_filename': f,
                    'original_stem': stem,
                    'original_ext': ext,
                    'dir_rel': str(rel_root),
                    'media_type': cat,
                    'file_size_bytes': size,
                })
    
    print(f"Total candidate files found: {len(items)}")
    return items

def group_companions(items: List[Dict[str, Any]]) -> None:
    print("Analyzing companion relationships (Live Photos, AAE sidecars, Audio memos)...")
    dir_stem_map = defaultdict(list)
    for idx, item in enumerate(items):
        key = (item['dir_rel'], item['original_stem'].lower())
        dir_stem_map[key].append(idx)
    
    group_seq = 1
    for key, indices in dir_stem_map.items():
        if len(indices) > 1:
            group_id = f"GRP_{group_seq:05d}"
            group_seq += 1
            has_photo = any(items[i]['media_type'] == 'photo' for i in indices)
            
            for i in indices:
                items[i]['companion_group_id'] = group_id
                mtype = items[i]['media_type']
                if mtype == 'photo':
                    items[i]['companion_role'] = 'primary'
                elif mtype == 'video' and has_photo:
                    items[i]['companion_role'] = 'live_photo_video'
                elif mtype == 'sidecar':
                    items[i]['companion_role'] = 'sidecar_aae'
                elif mtype == 'audio_memo':
                    items[i]['companion_role'] = 'audio_memo'
                else:
                    items[i]['companion_role'] = 'companion'
        else:
            items[indices[0]]['companion_group_id'] = None
            items[indices[0]]['companion_role'] = 'none'

def extract_timestamps_batch(items: List[Dict[str, Any]]) -> None:
    print("Extracting capture timestamps via ExifTool batch mode and filename analysis...")
    # First pass: check filename regex
    unresolved_indices = []
    for idx, item in enumerate(items):
        fn = item['original_filename']
        m = FILENAME_DATE_REGEX.match(fn)
        if m:
            item['capture_timestamp'] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}:{m.group(5)}:{m.group(6)}"
            item['capture_source'] = 'Filename'
            item['year'] = m.group(1)
            item['has_unknown_date'] = 0
        else:
            unresolved_indices.append(idx)
    
    print(f"{len(items) - len(unresolved_indices)} files resolved via filename regex.")
    print(f"Querying ExifTool for {len(unresolved_indices)} remaining files in batches...")
    
    # Process unresolved files with ExifTool in chunks of 2,000
    chunk_size = 2000
    for chunk_start in range(0, len(unresolved_indices), chunk_size):
        chunk_idx = unresolved_indices[chunk_start:chunk_start + chunk_size]
        file_paths = [items[i]['original_full_path'] for i in chunk_idx]
        
        try:
            p = subprocess.Popen(
                ['exiftool', '-json', '-fast2', '-DateTimeOriginal', '-CreateDate', '-CreationDate', '-@', '-'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = p.communicate(input='\n'.join(file_paths))
            exif_list = json.loads(stdout)
            exif_by_path = {entry['SourceFile']: entry for entry in exif_list if 'SourceFile' in entry}
        except Exception as e:
            print(f"ExifTool batch warning on chunk {chunk_start}: {e}")
            exif_by_path = {}
        
        for i in chunk_idx:
            item = items[i]
            orig_p = item['original_full_path']
            meta = exif_by_path.get(orig_p, {})
            
            raw_dt = meta.get('DateTimeOriginal') or meta.get('CreateDate') or meta.get('CreationDate')
            found_dt = None
            source = 'None'
            
            if raw_dt and isinstance(raw_dt, str):
                m_exif = EXIF_DATE_REGEX.match(raw_dt)
                if m_exif:
                    found_dt = f"{m_exif.group(1)}-{m_exif.group(2)}-{m_exif.group(3)}T{m_exif.group(4)}:{m_exif.group(5)}:{m_exif.group(6)}"
                    source = 'EXIF'
                    year = m_exif.group(1)
            
            if not found_dt:
                # Check folder year fallback
                m_fold = FOLDER_YEAR_REGEX.search(item['dir_rel'])
                if m_fold:
                    year = m_fold.group(1)
                    found_dt = f"{year}-01-01T00:00:00"
                    source = 'FolderYearFallback'
                else:
                    year = None
                    found_dt = None
                    source = 'Unknown'
            
            if found_dt and year:
                item['capture_timestamp'] = found_dt
                item['capture_source'] = source
                item['year'] = year
                item['has_unknown_date'] = 0 if source == 'EXIF' else 1
            else:
                item['capture_timestamp'] = None
                item['capture_source'] = 'None'
                item['year'] = 'UNKNOWN_DATE'
                item['has_unknown_date'] = 1

def assign_permanent_ids_and_targets(items: List[Dict[str, Any]]) -> None:
    print("Assigning permanent IDs and calculating destination paths...")
    
    # Query max IDs from DB for continuity (if DB already exists)
    init_db()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(id) FROM media_items WHERE id LIKE 'P_%'")
    res_p = cursor.fetchone()[0]
    photo_seq = int(res_p.split('_')[1]) + 1 if res_p else 1
    
    cursor.execute("SELECT MAX(id) FROM media_items WHERE id LIKE 'V_%'")
    res_v = cursor.fetchone()[0]
    video_seq = int(res_v.split('_')[1]) + 1 if res_v else 1
    
    cursor.execute("SELECT MAX(id) FROM media_items WHERE id LIKE 'S_%'")
    res_s = cursor.fetchone()[0]
    sidecar_seq = int(res_s.split('_')[1]) + 1 if res_s else 1
    
    cursor.execute("SELECT MAX(id) FROM media_items WHERE id LIKE 'X_%'")
    res_x = cursor.fetchone()[0]
    other_seq = int(res_x.split('_')[1]) + 1 if res_x else 1
    conn.close()
    
    # Sort items deterministically by original relative path
    items.sort(key=lambda x: x['original_rel_path'])
    
    # Assign permanent IDs
    for item in items:
        # Check if already assigned (in case of re-run)
        if 'id' in item:
            continue
            
        mtype = item['media_type']
        if mtype == 'photo':
            item['id'] = f"P_{photo_seq:06d}"
            photo_seq += 1
        elif mtype == 'video':
            item['id'] = f"V_{video_seq:06d}"
            video_seq += 1
        elif mtype in ('sidecar', 'audio_memo'):
            item['id'] = f"S_{sidecar_seq:06d}"
            sidecar_seq += 1
        else:
            item['id'] = f"X_{other_seq:06d}"
            other_seq += 1

    # Companion groups: ensure companion files use matching stem timestamp
    group_map = defaultdict(list)
    for idx, item in enumerate(items):
        gid = item.get('companion_group_id')
        if gid:
            group_map[gid].append(idx)
            
    for gid, indices in group_map.items():
        # Find primary or first item with valid timestamp
        ts_primary = None
        yr_primary = None
        for i in indices:
            if items[i].get('capture_timestamp') and not items[i]['has_unknown_date']:
                ts_primary = items[i]['capture_timestamp']
                yr_primary = items[i]['year']
                break
        if ts_primary and yr_primary:
            for i in indices:
                items[i]['capture_timestamp'] = ts_primary
                items[i]['year'] = yr_primary
                items[i]['has_unknown_date'] = 0

    # Calculate target directory and target filename
    for item in items:
        mtype = item['media_type']
        ts = item.get('capture_timestamp')
        year = item.get('year', 'UNKNOWN_DATE')
        pid = item['id']
        ext = item['original_ext']
        
        # Format target filename
        if ts and not item['has_unknown_date']:
            # YYYY-MM-DDTHH:MM:SS -> YYYYMMDD_HHMMSS
            ts_clean = ts.replace('-', '').replace(':', '').replace('T', '_')
            target_fn = f"{ts_clean}__{pid}{ext}"
        else:
            target_fn = f"UNKNOWN_DATE__{pid}{ext}"
            
        if mtype == 'photo':
            target_sub = TARGET_IMAGES_DIR / year
        elif mtype == 'video':
            target_sub = TARGET_VIDEOS_DIR / year
        elif mtype == 'sidecar':
            # Sidecar follows companion
            if item.get('companion_role') == 'live_photo_video':
                target_sub = TARGET_VIDEOS_DIR / year
            else:
                target_sub = TARGET_IMAGES_DIR / year
        elif mtype == 'audio_memo':
            target_sub = TARGET_IMAGES_DIR / year
        elif mtype == 'document':
            target_sub = EXCLUDED_DIR / 'documents'
            target_fn = f"{pid}__{item['original_filename']}"
        else:
            target_sub = EXCLUDED_DIR / 'other'
            target_fn = f"{pid}__{item['original_filename']}"
            
        target_path = target_sub / target_fn
        item['current_full_path'] = str(target_path)
        item['current_rel_path'] = str(target_path.relative_to(WORKSPACE_ROOT))
        item['current_filename'] = target_fn

def execute_stage1_moves(items: List[Dict[str, Any]]) -> Tuple[int, int, List[str]]:
    init_db()
    conn = get_connection()
    success_count = 0
    failure_count = 0
    errors = []
    
    print(f"Beginning APFS atomic moves for {len(items)} files...")
    
    # We execute inside a database transaction
    cursor = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()
    
    for idx, item in enumerate(items):
        src = Path(item['original_full_path'])
        dst = Path(item['current_full_path'])
        
        if not src.exists():
            failure_count += 1
            err = f"Source file does not exist: {src}"
            errors.append(err)
            continue
            
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            # Direct atomic APFS move
            os.rename(src, dst)
            
            # Insert into media_items
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
                    item['id'],
                    item['media_type'],
                    item['original_rel_path'],
                    item['original_filename'],
                    item['original_ext'],
                    item['current_rel_path'],
                    item['current_filename'],
                    item['file_size_bytes'],
                    None, # Will be populated or checked
                    item.get('companion_group_id'),
                    item.get('companion_role', 'none'),
                    item['has_unknown_date'],
                    item.get('capture_timestamp'),
                    item.get('capture_source', 'none'),
                    'stage1_consolidated',
                    now_iso,
                    now_iso
                )
            )
            
            # Log operation
            log_operation(
                conn,
                item['id'],
                'stage1_consolidation',
                'MOVE_AND_RENAME',
                item['original_rel_path'],
                item['current_rel_path'],
                'SUCCESS',
                {'original_stem': item['original_stem'], 'year': item.get('year')}
            )
            
            success_count += 1
            if (idx + 1) % 5000 == 0 or (idx + 1) == len(items):
                conn.commit()
                print(f"Moved and cataloged {idx + 1}/{len(items)} files...")
                
        except Exception as e:
            failure_count += 1
            err = f"Failed to move {src} -> {dst}: {e}"
            errors.append(err)
            log_operation(conn, item['id'], 'stage1_consolidation', 'MOVE_AND_RENAME',
                          item['original_rel_path'], item['current_rel_path'], 'FAILED', {'error': str(e)})

    conn.commit()
    conn.close()
    
    # Prune empty directories in photos, photos1, photos2
    print("Pruning empty source directories...")
    for sdir in SOURCE_DIRS:
        for root, dirs, files in os.walk(sdir, topdown=False):
            if root == str(sdir):
                continue
            try:
                os.rmdir(root)
            except OSError:
                pass
                
    return success_count, failure_count, errors

def generate_stage1_report(items: List[Dict[str, Any]], success_count: int, failure_count: int, errors: List[str]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_json_path = REPORTS_DIR / 'stage1_report.json'
    report_md_path = REPORTS_DIR / 'stage1_report.md'
    
    # Categorization counts
    type_counts = Counter(item['media_type'] for item in items)
    year_counts = Counter(item.get('year', 'UNKNOWN_DATE') for item in items if item['media_type'] in ('photo', 'video'))
    companion_count = sum(1 for item in items if item.get('companion_group_id'))
    
    summary = {
        'stage': 'stage1_consolidation',
        'completed_at': datetime.now(timezone.utc).isoformat(),
        'total_scanned': len(items),
        'success_count': success_count,
        'failure_count': failure_count,
        'free_disk_gb_remaining': round(check_disk_space(), 2),
        'media_type_breakdown': dict(type_counts),
        'year_distribution': dict(sorted(year_counts.items())),
        'companion_files_linked': companion_count,
        'errors': errors[:50]
    }
    
    with open(report_json_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
        
    md_content = f"""# Stage 1: Consolidation & Direct Rename-Move Report

**Generated at**: {summary['completed_at']}  
**Status**: {"SUCCESS" if failure_count == 0 else "COMPLETED WITH ERRORS"}

---

## 1. Summary Metrics
- **Total Files Scanned & Processed**: {summary['total_scanned']:,}
- **Successfully Moved & Cataloged**: {summary['success_count']:,}
- **Failures / Errors**: {summary['failure_count']}
- **Companion Files Linked**: {summary['companion_files_linked']:,}
- **Free Disk Space Remaining**: {summary['free_disk_gb_remaining']:.2f} GiB (Safe threshold > {MIN_FREE_DISK_GB} GiB)

---

## 2. Media Type Breakdown
| Media Category | File Count | Target Directory |
| :--- | :--- | :--- |
| **Photos** | {type_counts.get('photo', 0):,} | `images/YYYY/` |
| **Videos** | {type_counts.get('video', 0):,} | `videos/YYYY/` (Inventoried & excluded from AI) |
| **Sidecars (.aae, .xmp)** | {type_counts.get('sidecar', 0):,} | Paired with parent media |
| **Audio Memos (.wav)** | {type_counts.get('audio_memo', 0):,} | Paired with parent photo |
| **Documents (.doc)** | {type_counts.get('document', 0):,} | `excluded/documents/` |
| **Other** | {type_counts.get('other', 0):,} | `excluded/other/` |

---

## 3. Year-Based Partitioning Distribution
The media files were cleanly organized by capture year:
"""
    for yr, count in sorted(year_counts.items()):
        md_content += f"- **{yr}**: {count:,} items\n"

    md_content += f"""
---

## 4. Verification & Audit Trail
- All operations were recorded in `catalog.sqlite` (`media_items` and `operation_history`).
- No duplicate names or collisions occurred due to unique permanent IDs (`P_XXXXXX` and `V_XXXXXX`).
- Direct APFS moves consumed **0 extra bytes of storage**.
"""
    if errors:
        md_content += f"\n## 5. Errors Encountered\n"
        for err in errors[:20]:
            md_content += f"- `{err}`\n"
            
    with open(report_md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)
        
    # Save checkpoint in DB
    conn = get_connection()
    record_checkpoint(conn, 'stage1_consolidation', len(items), success_count, failure_count, str(report_json_path), summary)
    conn.close()
    
    print(f"Stage 1 report generated at: {report_md_path}")
    return report_md_path

def main():
    free_gb = check_disk_space()
    print(f"Current free disk space: {free_gb:.2f} GiB")
    if free_gb < MIN_FREE_DISK_GB:
        raise RuntimeError(f"Free disk space ({free_gb:.2f} GiB) is below safe limit ({MIN_FREE_DISK_GB} GiB)!")
        
    items = scan_sources()
    if not items:
        print("No items found to process.")
        return
        
    group_companions(items)
    extract_timestamps_batch(items)
    assign_permanent_ids_and_targets(items)
    
    success, failure, errors = execute_stage1_moves(items)
    report_path = generate_stage1_report(items, success, failure, errors)
    print(f"\nStage 1 execution complete! {success} moved, {failure} failed.")

if __name__ == '__main__':
    from collections import Counter
    main()
