import os
import sys
from pathlib import Path
_viewer_root = Path(__file__).resolve().parent.parent
if str(_viewer_root) not in sys.path:
    sys.path.insert(0, str(_viewer_root))

import sqlite3
import json
import time

from pipeline.config import WORKSPACE_ROOT, VIEWER_DIR, DB_PATH, JSON_CATALOG_PATH

def export_all():
    print("=" * 60)
    print("RE-EXPORTING CATALOG DATA & VIEWER PAYLOAD")
    print("=" * 60)
    
    t0 = time.time()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Query media items
    print("Querying master photos from media_items...")
    cursor.execute("""
        SELECT 
            m.id,
            p.processing_path,
            m.current_path,
            COALESCE(m.capture_timestamp, ''),
            COALESCE(e.city, ''),
            COALESCE(e.country, ''),
            COALESCE(a.short_caption, ''),
            COALESCE(a.is_document_or_homework, 0),
            COALESCE(e.model, '')
        FROM media_items m
        JOIN processing_inputs p ON m.id = p.media_id
        LEFT JOIN metadata_exif e ON m.id = e.media_id
        LEFT JOIN ai_descriptions a ON m.id = a.media_id
        WHERE m.media_type = 'photo'
        ORDER BY m.capture_timestamp DESC
    """)
    rows = cursor.fetchall()
    
    # 2. Query face clusters
    print("Mapping ArcFace clusters from people_faces...")
    cursor.execute("SELECT DISTINCT media_id, cluster_id FROM people_faces WHERE cluster_id >= 0")
    face_clusters = {}
    for mid, cid in cursor.fetchall():
        if mid not in face_clusters:
            face_clusters[mid] = []
        face_clusters[mid].append(cid)
            
    items = []
    for r in rows:
        mid = r[0]
        proc = r[1]
        cur = r[2]
        
        # Convert path to relative for viewer/viewer.html
        if proc.startswith('viewer/'):
            p_web = proc.replace('viewer/', '', 1)
        elif proc.startswith('content/'):
            p_web = '../' + proc
        else:
            p_web = proc
            
        if cur.startswith('content/'):
            orig_web = '../' + cur
        else:
            orig_web = cur
            
        loc = f"{r[4]}, {r[5]}" if r[4] else ""
        items.append({
            'id': mid,
            'p': p_web,
            'orig': orig_web,
            'd': r[3][:10] if r[3] else '',
            'l': loc,
            'c': r[6],
            'hw': bool(r[7]),
            'cam': r[8],
            'f': face_clusters.get(mid, [])
        })
        
    # 3. Locations for World Map
    cursor.execute("""
        SELECT city, country, AVG(gps_latitude), AVG(gps_longitude), COUNT(*)
        FROM metadata_exif
        WHERE gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL AND city IS NOT NULL
        GROUP BY city, country
        ORDER BY COUNT(*) DESC
        LIMIT 35
    """)
    locations = [{'city': r[0], 'country': r[1], 'lat': round(r[2], 5), 'lon': round(r[3], 5), 'count': r[4]} for r in cursor.fetchall()]
    
    # 4. Core 9 Clusters & Extended Clusters
    cursor.execute("""
        SELECT cluster_id, COUNT(*), MAX(person_name)
        FROM people_faces 
        WHERE cluster_id >= 0
        GROUP BY cluster_id 
        ORDER BY cluster_id ASC 
        LIMIT 30
    """)
    clust_rows = cursor.fetchall()
    core_clusters = [{'id': r[0], 'count': r[1], 'name': r[2] or f"Family Member {r[0]+1}"} for r in clust_rows[:9]]
    extended_clusters = [{'id': r[0], 'count': r[1], 'name': r[2] or f"Recurring Person {r[0]+1}"} for r in clust_rows[9:30]]
    
    conn.close()
    
    # Write catalog_data.js
    data_js_path = VIEWER_DIR / 'catalog_data.js'
    print(f"Writing browser payload to {data_js_path}...")
    with open(data_js_path, 'w', encoding='utf-8') as f:
        f.write("// Auto-generated Catalog Data for Offline Museum Viewer\n")
        f.write(f"const ALL_PHOTOS = {json.dumps(items)};\n\n")
        f.write(f"const MAP_LOCATIONS = {json.dumps(locations)};\n\n")
        f.write(f"const CORE_CLUSTERS = {json.dumps(core_clusters)};\n\n")
        f.write(f"const EXTENDED_CLUSTERS = {json.dumps(extended_clusters)};\n")
        
    js_size_mb = data_js_path.stat().st_size / (1024 * 1024)
    print(f"✓ Exported catalog_data.js in {time.time() - t0:.2f}s ({len(items):,} items, {js_size_mb:.2f} MB)!")

if __name__ == '__main__':
    export_all()
