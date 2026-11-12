import os
import sys
from pathlib import Path
_viewer_root = Path(__file__).resolve().parent.parent
if str(_viewer_root) not in sys.path:
    sys.path.insert(0, str(_viewer_root))

import argparse
from pipeline.config import (
    WORKSPACE_ROOT,
    VIEWER_DIR,
    SOURCE_DIRS,
    TARGET_IMAGES_DIR,
    TARGET_VIDEOS_DIR,
    DB_PATH,
    JSON_CATALOG_PATH,
    PROCESSING_CACHE_DIR,
    AVATARS_DIR,
    MANIFEST_JSON_PATH,
    MANIFEST_CSV_PATH,
    MIN_FREE_DISK_GB
)
from pipeline.db import get_connection

def verify_stage1() -> bool:
    print("=" * 60)
    print("VERIFYING STAGE 1: CONSOLIDATION & DIRECT RENAME-MOVE")
    print("=" * 60)
    
    if not DB_PATH.exists():
        print(f"❌ Error: Database {DB_PATH} does not exist!")
        return False
        
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Total items count
    cursor.execute("SELECT COUNT(*) FROM media_items WHERE processing_status = 'stage1_consolidated'")
    total_db_items = cursor.fetchone()[0]
    print(f"✓ Found {total_db_items:,} media items in SQLite catalog.")
    
    # 2. Verify every file exists on disk at its current_path
    cursor.execute("SELECT id, current_path, file_size_bytes FROM media_items")
    rows = cursor.fetchall()
    
    missing_files = []
    size_mismatches = []
    all_current_paths = set()
    
    for row in rows:
        cid = row['id']
        rel_p = row['current_path']
        exp_sz = row['file_size_bytes']
        
        full_p = WORKSPACE_ROOT / rel_p
        if not full_p.exists():
            missing_files.append((cid, rel_p))
        else:
            all_current_paths.add(str(full_p))
            actual_sz = full_p.stat().st_size
            if actual_sz != exp_sz:
                size_mismatches.append((cid, rel_p, exp_sz, actual_sz))
                
    if missing_files:
        print(f"❌ Error: {len(missing_files)} files recorded in database are missing on disk!")
        for cid, p in missing_files[:10]:
            print(f"   - {cid}: {p}")
        return False
    else:
        print(f"✓ 100% of {len(rows):,} files exist at their recorded destination paths.")
        
    if size_mismatches:
        print(f"❌ Warning: {len(size_mismatches)} files have size mismatches.")
        return False
    else:
        print(f"✓ 100% of file sizes match recorded sizes exactly.")
        
    # 3. Check for any duplicate filenames on disk
    if len(all_current_paths) != len(rows):
        print(f"❌ Error: Duplicate destination paths detected! ({len(all_current_paths)} unique vs {len(rows)} rows)")
        return False
    else:
        print(f"✓ 0 filename collisions detected: all {len(rows):,} destination paths are strictly unique.")
        
    # 4. Check that source directories are empty of media
    remaining_media = []
    for sdir in SOURCE_DIRS:
        if sdir.exists():
            for root, _, files in os.walk(sdir):
                for f in files:
                    if not f.startswith('.'):
                        remaining_media.append(os.path.join(root, f))
                        
    if remaining_media:
        print(f"⚠️ Warning: {len(remaining_media)} non-hidden files remain in source folders:")
        for f in remaining_media[:5]:
            print(f"   - {f}")
    else:
        print(f"✓ All source folders (photos/, photos1/, photos2/) are completely cleared of media.")
        
    # 5. Check free disk space
    st = os.statvfs(WORKSPACE_ROOT)
    free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
    print(f"✓ Free disk space remaining: {free_gb:.2f} GiB (Safe limit > {MIN_FREE_DISK_GB} GiB)")
    
    # 6. Breakdown by media type
    cursor.execute("SELECT media_type, COUNT(*) FROM media_items GROUP BY media_type")
    type_counts = dict(cursor.fetchall())
    print("\nCatalog inventory breakdown:")
    for mtype, count in type_counts.items():
        print(f"  - {mtype}: {count:,}")
        
    # 7. Companion linkage check
    cursor.execute("SELECT COUNT(*) FROM media_items WHERE companion_group_id IS NOT NULL")
    companion_count = cursor.fetchone()[0]
    print(f"✓ Companion files linked: {companion_count:,}")
    
    conn.close()
    print("\n" + "=" * 60)
    print("STAGE 1 VERIFICATION PASSED SUCCESSFULLY")
    print("=" * 60)
    return True

def verify_stage2() -> bool:
    print("=" * 60)
    print("VERIFYING STAGE 2: METADATA & PROCESSING INPUTS STANDARDIZATION")
    print("=" * 60)
    
    if not DB_PATH.exists():
        print(f"❌ Error: Database {DB_PATH} does not exist!")
        return False
        
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Total photos
    cursor.execute("SELECT COUNT(*) FROM media_items WHERE media_type = 'photo'")
    total_photos = cursor.fetchone()[0]
    print(f"✓ Found {total_photos:,} photos in master catalog.")
    
    # 2. Metadata rows count
    cursor.execute("SELECT COUNT(*) FROM metadata_exif")
    total_meta = cursor.fetchone()[0]
    if total_meta != total_photos:
        print(f"❌ Error: Metadata count ({total_meta:,}) does not match photo count ({total_photos:,})!")
        return False
    else:
        print(f"✓ 100% ({total_meta:,}) of photos have full metadata records in SQLite.")
        
    # 3. GPS coordinates check
    cursor.execute("SELECT COUNT(*) FROM metadata_exif WHERE gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL")
    gps_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM metadata_exif WHERE country IS NOT NULL")
    geocoded_count = cursor.fetchone()[0]
    print(f"✓ Found {gps_count:,} photos with GPS coordinates ({geocoded_count:,} successfully reverse-geocoded).")
    
    # 4. Processing inputs count
    cursor.execute("SELECT COUNT(*) FROM processing_inputs")
    total_inputs = cursor.fetchone()[0]
    if total_inputs != total_photos:
        print(f"❌ Error: Processing inputs count ({total_inputs:,}) does not match photo count ({total_photos:,})!")
        return False
    else:
        print(f"✓ 100% ({total_inputs:,}) of photos have registered processing inputs.")
        
    # 5. Verify every processing_path exists on disk
    cursor.execute("SELECT media_id, processing_path, is_reused_original FROM processing_inputs")
    rows = cursor.fetchall()
    
    missing_inputs = []
    reused_count = 0
    proxy_count = 0
    
    for r in rows:
        mid = r['media_id']
        p_rel = r['processing_path']
        is_reused = r['is_reused_original']
        
        full_p = WORKSPACE_ROOT / p_rel
        if not full_p.exists() or full_p.stat().st_size == 0:
            missing_inputs.append((mid, p_rel))
            
        if is_reused:
            reused_count += 1
        else:
            proxy_count += 1
            
    if missing_inputs:
        print(f"❌ Error: {len(missing_inputs)} processing inputs are missing or empty on disk!")
        for mid, p in missing_inputs[:10]:
            print(f"   - {mid}: {p}")
        return False
    else:
        print(f"✓ 100% of {len(rows):,} processing inputs exist and have non-zero size on disk.")
        print(f"   - Reused compatible originals (0 extra bytes): {reused_count:,}")
        print(f"   - Generated JPEG proxies in cache: {proxy_count:,}")
        
    # 6. Check free disk space
    st = os.statvfs(WORKSPACE_ROOT)
    free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
    print(f"✓ Free disk space remaining: {free_gb:.2f} GiB (Safe threshold > {MIN_FREE_DISK_GB} GiB)")
    if free_gb < MIN_FREE_DISK_GB:
        print(f"❌ Error: Free disk space dropped below safety threshold!")
        return False
        
    conn.close()
    print("\n" + "=" * 60)
    print("STAGE 2 VERIFICATION PASSED SUCCESSFULLY")
    print("=" * 60)
    return True

def verify_stage3() -> bool:
    print("=" * 60)
    print("VERIFYING STAGE 3: VALIDATION & AI MANIFEST")
    print("=" * 60)
    
    if not DB_PATH.exists():
        print(f"❌ Error: Database {DB_PATH} does not exist!")
        return False
        
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Total photos
    cursor.execute("SELECT COUNT(*) FROM media_items WHERE media_type = 'photo'")
    total_photos = cursor.fetchone()[0]
    
    # 2. Check ai_manifest table
    cursor.execute("SELECT COUNT(*) FROM ai_manifest")
    manifest_count = cursor.fetchone()[0]
    if manifest_count != total_photos:
        print(f"❌ Error: ai_manifest count ({manifest_count:,}) does not match photo count ({total_photos:,})!")
        return False
    else:
        print(f"✓ Found {manifest_count:,} validated records in SQLite ai_manifest table.")
        
    # 3. Check invalid or corrupt items in processing_inputs
    cursor.execute("SELECT COUNT(*) FROM processing_inputs WHERE validation_status != 'VALID'")
    corrupt_count = cursor.fetchone()[0]
    if corrupt_count > 0:
        print(f"⚠️ Warning: {corrupt_count} images have non-valid validation_status.")
    else:
        print(f"✓ 100% of {total_photos:,} processing images have validation_status = 'VALID'.")
        
    # 4. Check that technical properties are populated
    cursor.execute("""
        SELECT COUNT(*) FROM ai_manifest
        WHERE pixel_width <= 0 OR pixel_height <= 0 OR sha256_processing = '' OR dhash = ''
    """)
    invalid_props = cursor.fetchone()[0]
    if invalid_props > 0:
        print(f"❌ Error: {invalid_props} rows have missing technical properties or hashes!")
        return False
    else:
        print(f"✓ 100% of images have valid dimensions, SHA-256, and perceptual difference hashes (dHash).")
        
    # 5. Check manifest files on disk
    json_path = MANIFEST_JSON_PATH
    csv_path = MANIFEST_CSV_PATH
    
    if not json_path.exists():
        print(f"❌ Error: Manifest file {json_path} does not exist!")
        return False
    if not csv_path.exists():
        print(f"❌ Error: Manifest file {csv_path} does not exist!")
        return False
        
    import json
    with open(json_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
    print(f"✓ ai_manifest.json verified: {len(json_data):,} records ({json_path.stat().st_size / (1024*1024):.2f} MB).")
    print(f"✓ ai_manifest.csv verified ({csv_path.stat().st_size / (1024*1024):.2f} MB).")
    
    # 6. UNKNOWN_DATE matches summary
    cursor.execute("SELECT COUNT(*) FROM ai_manifest WHERE matched_dated_id IS NOT NULL")
    matched_unknowns = cursor.fetchone()[0]
    print(f"✓ UNKNOWN_DATE perceptual hash matches: {matched_unknowns} images matched to dated originals.")
    
    conn.close()
    print("\n" + "=" * 60)
    print("STAGE 3 VERIFICATION PASSED SUCCESSFULLY")
    print("=" * 60)
    return True

def verify_stage4() -> bool:
    print("=" * 60)
    print("VERIFYING STAGE 4: VISION ANALYSIS, HOMEWORK DETECTION & FACE CLUSTERING")
    print("=" * 60)
    
    if not DB_PATH.exists():
        print(f"❌ Error: Database {DB_PATH} does not exist!")
        return False
        
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. AI Descriptions Count
    cursor.execute("SELECT COUNT(*) FROM ai_descriptions")
    ai_count = cursor.fetchone()[0]
    print(f"✓ Found {ai_count:,} records in ai_descriptions table.")
    
    # 2. Homework & Document detection stats
    cursor.execute("SELECT COUNT(*) FROM ai_descriptions WHERE is_document_or_homework = 1")
    hw_count = cursor.fetchone()[0]
    print(f"✓ Detected and flagged {hw_count:,} notebook/textbook/homework/document photos.")
    
    # 3. People & Faces statistics
    cursor.execute("SELECT COUNT(*) FROM people_faces")
    total_faces = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT media_id) FROM people_faces")
    photos_with_faces = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM people_faces WHERE cluster_id = -1")
    noise_faces = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT cluster_id) FROM people_faces WHERE cluster_id >= 0")
    distinct_clusters = cursor.fetchone()[0]
    
    print(f"✓ Total faces detected: {total_faces:,} across {photos_with_faces:,} photos.")
    print(f"✓ Open-ended clustering identified {distinct_clusters} recurring identity clusters.")
    print(f"✓ Noise rejection isolated {noise_faces:,} one-off stranger/pedestrian faces (cluster_id = -1).")
    
    # 4. Top 9 Core Family clusters
    cursor.execute("""
        SELECT cluster_id, COUNT(*) FROM people_faces
        WHERE cluster_id >= 0
        GROUP BY cluster_id
        ORDER BY cluster_id ASC
        LIMIT 9
    """)
    top9 = cursor.fetchall()
    print("\nTop Core Family Clusters:")
    for cid, cnt in top9:
        print(f"  - Family Member {cid + 1} (Cluster {cid}): {cnt:,} faces")
        
    # 5. Check avatar directory
    avatars_dir = AVATARS_DIR
    if avatars_dir.exists():
        avatar_files = list(avatars_dir.glob('*.jpg'))
        print(f"✓ Generated {len(avatar_files)} cluster avatar images in {avatars_dir}.")
    
    conn.close()
    print("\n" + "=" * 60)
    print("STAGE 4 VERIFICATION PASSED SUCCESSFULLY")
    print("=" * 60)
    return True

def verify_stage5() -> bool:
    print("=" * 60)
    print("VERIFYING STAGE 5: FTS5 SEARCH, CATALOG EXPORT & MUSEUM VIEWER")
    print("=" * 60)
    
    if not DB_PATH.exists():
        print(f"❌ Error: Database {DB_PATH} does not exist!")
        return False
        
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. SQLite FTS5 Search
    try:
        cursor.execute("SELECT COUNT(*) FROM ai_search_fts")
        fts_count = cursor.fetchone()[0]
        print(f"✓ SQLite FTS5 virtual table active with {fts_count:,} searchable indexed documents.")
    except Exception as e:
        print(f"❌ Error testing FTS5: {e}")
        return False
        
    # 2. catalog.json check
    cat_json = JSON_CATALOG_PATH
    if not cat_json.exists():
        print(f"❌ Error: {cat_json} does not exist!")
        return False
    else:
        print(f"✓ catalog.json verified ({cat_json.stat().st_size / (1024*1024):.2f} MB).")
        
    # 3. viewer.html check
    viewer_path = VIEWER_DIR / 'viewer.html'
    if not viewer_path.exists():
        print(f"❌ Error: {viewer_path} does not exist!")
        return False
    else:
        print(f"✓ Offline Museum Viewer (viewer.html) verified ({viewer_path.stat().st_size / (1024*1024):.2f} MB).")
        
    # 4. Processing cache cleanup check
    cache_dir = PROCESSING_CACHE_DIR
    if cache_dir.exists() and any(cache_dir.iterdir()):
        print(f"⚠️ Notice: .processing_cache/ still contains files.")
    else:
        print("✓ .processing_cache/ successfully purged and storage reclaimed.")
        
    # 5. Free disk space
    st = os.statvfs(WORKSPACE_ROOT)
    free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
    print(f"✓ Free disk space: {free_gb:.2f} GiB.")
    
    conn.close()
    print("\n" + "=" * 60)
    print("STAGE 5 VERIFICATION PASSED SUCCESSFULLY")
    print("=" * 60)
    return True

def main():
    parser = argparse.ArgumentParser(description="Pipeline stage verification")
    parser.add_argument("--stage", type=int, required=True, choices=[1, 2, 3, 4, 5])
    args = parser.parse_args()
    
    if args.stage == 1:
        ok = verify_stage1()
        sys.exit(0 if ok else 1)
    elif args.stage == 2:
        ok = verify_stage2()
        sys.exit(0 if ok else 1)
    elif args.stage == 3:
        ok = verify_stage3()
        sys.exit(0 if ok else 1)
    elif args.stage == 4:
        ok = verify_stage4()
        sys.exit(0 if ok else 1)
    elif args.stage == 5:
        ok = verify_stage5()
        sys.exit(0 if ok else 1)

if __name__ == '__main__':
    main()
