import os
import sys
from pathlib import Path
_viewer_root = Path(__file__).resolve().parent.parent
if str(_viewer_root) not in sys.path:
    sys.path.insert(0, str(_viewer_root))

import json
import sqlite3
import shutil
from datetime import datetime, timezone
from typing import Dict, Any, List

from pipeline.config import WORKSPACE_ROOT, VIEWER_DIR, DB_PATH, JSON_CATALOG_PATH, PROCESSING_CACHE_DIR, REPORTS_DIR
from pipeline.db import get_connection, log_operation, record_checkpoint
from pipeline.export_viewer_data import export_all as export_catalog_data_js

def build_fts5_index(conn: sqlite3.Connection):
    print("Building SQLite FTS5 Full-Text Search Virtual Table...")
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS ai_search_fts")
    cursor.execute("""
        CREATE VIRTUAL TABLE ai_search_fts USING fts5(
            media_id UNINDEXED,
            short_caption,
            detailed_description,
            tags,
            scene_type,
            camera_model,
            city,
            country,
            capture_timestamp
        )
    """)
    
    cursor.execute("""
        INSERT INTO ai_search_fts (
            media_id, short_caption, detailed_description, tags, scene_type,
            camera_model, city, country, capture_timestamp
        )
        SELECT 
            m.id,
            COALESCE(a.short_caption, ''),
            COALESCE(a.detailed_description, ''),
            COALESCE(a.tags, ''),
            COALESCE(a.scene_type, ''),
            COALESCE(e.model, ''),
            COALESCE(e.city, ''),
            COALESCE(e.country, ''),
            COALESCE(m.capture_timestamp, '')
        FROM media_items m
        LEFT JOIN ai_descriptions a ON m.id = a.media_id
        LEFT JOIN metadata_exif e ON m.id = e.media_id
        WHERE m.media_type = 'photo'
    """)
    conn.commit()
    cursor.execute("SELECT COUNT(*) FROM ai_search_fts")
    count = cursor.fetchone()[0]
    print(f"✓ FTS5 virtual table created with {count:,} indexed records.")

def export_catalog_json(conn: sqlite3.Connection) -> Path:
    print(f"Exporting full catalog to {JSON_CATALOG_PATH}...")
    cursor = conn.cursor()
    
    # Query all master data joined
    cursor.execute("""
        SELECT 
            m.id, m.media_type, m.current_path, m.file_size_bytes,
            m.capture_timestamp, m.has_unknown_date, m.companion_group_id,
            e.make, e.model, e.gps_latitude, e.gps_longitude, e.city, e.state, e.country,
            p.pixel_width, p.pixel_height, p.aspect_ratio, p.megapixels, p.format,
            a.short_caption, a.detailed_description, a.tags, a.scene_type,
            a.primary_subjects, a.is_document_or_homework
        FROM media_items m
        LEFT JOIN metadata_exif e ON m.id = e.media_id
        LEFT JOIN processing_inputs p ON m.id = p.media_id
        LEFT JOIN ai_descriptions a ON m.id = a.media_id
        ORDER BY m.capture_timestamp ASC, m.id ASC
    """)
    
    rows = cursor.fetchall()
    
    # Query face clusters
    cursor.execute("SELECT media_id, cluster_id, person_name, confidence FROM people_faces")
    face_map = {}
    for r in cursor.fetchall():
        mid, cid, name, conf = r
        if mid not in face_map:
            face_map[mid] = []
        face_map[mid].append({'cluster_id': cid, 'person_name': name, 'confidence': conf})
        
    items = []
    for r in rows:
        tags_list = json.loads(r[21]) if r[21] else []
        subjects_list = json.loads(r[23]) if r[23] else []
        mid = r[0]
        
        items.append({
            'id': mid,
            'media_type': r[1],
            'path': r[2],
            'file_size': r[3],
            'timestamp': r[4],
            'has_unknown_date': bool(r[5]),
            'companion_group': r[6],
            'camera': f"{r[7] or ''} {r[8] or ''}".strip() or None,
            'gps': {'lat': r[9], 'lon': r[10]} if r[9] is not None and r[10] is not None else None,
            'location': {'city': r[11], 'state': r[12], 'country': r[13]} if r[13] else None,
            'dimensions': {'w': r[14], 'h': r[15], 'aspect': r[16], 'mp': r[17], 'format': r[18]},
            'ai': {
                'caption': r[19] or '',
                'description': r[20] or '',
                'tags': tags_list,
                'scene_type': r[22] or 'general',
                'primary_subjects': subjects_list,
                'is_document_or_homework': bool(r[24]) if r[24] is not None else False
            },
            'faces': face_map.get(mid, [])
        })
        
    with open(JSON_CATALOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(items, f, indent=2)
        
    size_mb = JSON_CATALOG_PATH.stat().st_size / (1024 * 1024)
    print(f"✓ Exported {len(items):,} items to catalog.json ({size_mb:.2f} MB).")
    return JSON_CATALOG_PATH

def generate_viewer_html(conn: sqlite3.Connection = None) -> Path:
    viewer_path = VIEWER_DIR / 'viewer.html'
    if viewer_path.exists():
        try:
            with open(viewer_path, 'r', encoding='utf-8') as f:
                existing = f.read()
            if 'catalog_data.js' in existing:
                print(f"✓ Preserving existing Offline Museum Viewer: {viewer_path}")
                return viewer_path
        except Exception:
            pass
            
    print(f"Generating Offline Museum Viewer application at {viewer_path}...")
    
    if conn is None:
        conn = get_connection()
    
    # 1. Stats
    cursor.execute("SELECT COUNT(*) FROM media_items WHERE media_type = 'photo'")
    total_photos = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM metadata_exif WHERE country IS NOT NULL")
    geocoded_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM ai_descriptions WHERE is_document_or_homework = 1")
    homework_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT cluster_id) FROM people_faces WHERE cluster_id >= 0")
    cluster_count = cursor.fetchone()[0]
    
    # 2. Top locations for Map
    cursor.execute("""
        SELECT city, country, AVG(gps_latitude), AVG(gps_longitude), COUNT(*)
        FROM metadata_exif
        WHERE gps_latitude IS NOT NULL AND gps_longitude IS NOT NULL AND city IS NOT NULL
        GROUP BY city, country
        ORDER BY COUNT(*) DESC
        LIMIT 35
    """)
    locations = []
    for r in cursor.fetchall():
        locations.append({
            'city': r[0],
            'country': r[1],
            'lat': round(r[2], 5),
            'lon': round(r[3], 5),
            'count': r[4]
        })
        
    # 3. Top 9 Core Family Clusters
    cursor.execute("""
        SELECT cluster_id, COUNT(*) 
        FROM people_faces 
        WHERE cluster_id >= 0
        GROUP BY cluster_id 
        ORDER BY cluster_id ASC 
        LIMIT 9
    """)
    top_clusters = [{'id': r[0], 'count': r[1], 'name': f"Family Member {r[0]+1}"} for r in cursor.fetchall()]
    
    # 4. Sample Photos for Fast Initial UI Rendering
    cursor.execute("""
        SELECT m.id, m.current_path, m.capture_timestamp, e.city, e.country,
               COALESCE(a.short_caption, ''), COALESCE(a.is_document_or_homework, 0),
               p.pixel_width, p.pixel_height
        FROM media_items m
        LEFT JOIN metadata_exif e ON m.id = e.media_id
        LEFT JOIN ai_descriptions a ON m.id = a.media_id
        LEFT JOIN processing_inputs p ON m.id = p.media_id
        WHERE m.media_type = 'photo'
        ORDER BY m.capture_timestamp DESC
        LIMIT 120
    """)
    sample_photos = []
    for r in cursor.fetchall():
        sample_photos.append({
            'id': r[0],
            'path': r[1],
            'date': r[2][:10] if r[2] else 'Unknown',
            'loc': f"{r[3]}, {r[4]}" if r[3] else '',
            'caption': r[5],
            'is_hw': bool(r[6]),
            'w': r[7],
            'h': r[8]
        })
        
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Offline Media Archive & Museum Viewer</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <style>
    :root {{
      --bg: #0f1117;
      --card-bg: #1a1d26;
      --border: #2a2e3d;
      --text: #e2e8f0;
      --text-muted: #94a3b8;
      --accent: #3b82f6;
      --accent-hover: #2563eb;
      --danger: #ef4444;
      --danger-hover: #dc2626;
      --warning: #f59e0b;
      --success: #10b981;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }}
    /* Top Header */
    header {{
      background: var(--card-bg);
      border-bottom: 1px solid var(--border);
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
    }}
    .logo {{
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 700;
      font-size: 1.15rem;
      letter-spacing: -0.02em;
    }}
    .badge {{
      background: rgba(59, 130, 246, 0.15);
      color: #60a5fa;
      padding: 4px 10px;
      border-radius: 9999px;
      font-size: 0.8rem;
      font-weight: 600;
    }}
    /* Metrics Pills */
    .metrics {{
      display: flex;
      gap: 16px;
      font-size: 0.85rem;
    }}
    .metric-pill {{
      background: rgba(255,255,255,0.05);
      border: 1px solid var(--border);
      padding: 6px 14px;
      border-radius: 8px;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .metric-pill strong {{ color: #fff; }}
    /* Main Tabs Bar */
    nav.tabs {{
      background: #141720;
      border-bottom: 1px solid var(--border);
      display: flex;
      padding: 0 24px;
      gap: 8px;
      flex-shrink: 0;
    }}
    .tab-btn {{
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 12px 18px;
      font-size: 0.9rem;
      font-weight: 600;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      display: flex;
      align-items: center;
      gap: 8px;
      transition: all 0.15s ease;
    }}
    .tab-btn:hover {{ color: var(--text); }}
    .tab-btn.active {{
      color: var(--accent);
      border-bottom-color: var(--accent);
    }}
    .tab-badge {{
      background: rgba(239, 68, 68, 0.2);
      color: #fca5a5;
      padding: 2px 6px;
      border-radius: 6px;
      font-size: 0.75rem;
    }}
    /* View Containers */
    main {{
      flex: 1;
      overflow: hidden;
      position: relative;
    }}
    .tab-pane {{
      display: none;
      height: 100%;
      width: 100%;
      overflow-y: auto;
      padding: 24px;
    }}
    .tab-pane.active {{ display: block; }}
    
    /* Search Bar */
    .search-box {{
      position: sticky;
      top: 0;
      z-index: 10;
      background: var(--bg);
      padding-bottom: 16px;
      display: flex;
      gap: 12px;
    }}
    .search-input {{
      flex: 1;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 16px;
      color: var(--text);
      font-size: 0.95rem;
      outline: none;
    }}
    .search-input:focus {{ border-color: var(--accent); }}
    
    /* Grid Gallery */
    .photo-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 16px;
    }}
    .photo-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
      cursor: pointer;
      transition: transform 0.15s, border-color 0.15s;
      position: relative;
    }}
    .photo-card:hover {{
      transform: translateY(-3px);
      border-color: #3b82f6;
    }}
    .card-thumb {{
      width: 100%;
      aspect-ratio: 4/3;
      object-fit: cover;
      background: #000;
      display: block;
    }}
    .card-info {{
      padding: 10px 12px;
    }}
    .card-title {{
      font-size: 0.82rem;
      line-height: 1.3;
      margin-bottom: 6px;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .card-meta {{
      font-size: 0.72rem;
      color: var(--text-muted);
      display: flex;
      justify-content: space-between;
    }}
    
    /* Map Container */
    #map {{
      width: 100%;
      height: 100%;
      border-radius: 12px;
    }}
    
    /* People Tab */
    .people-section-title {{
      font-size: 1.25rem;
      font-weight: 700;
      margin-bottom: 16px;
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .people-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 20px;
      margin-bottom: 32px;
    }}
    .person-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      text-align: center;
      transition: all 0.2s ease;
      cursor: pointer;
    }}
    .person-card:hover {{
      border-color: var(--accent);
      transform: translateY(-2px);
    }}
    .person-avatar {{
      width: 96px;
      height: 96px;
      border-radius: 50%;
      object-fit: cover;
      margin: 0 auto 12px;
      border: 2px solid var(--accent);
      background: #2a2e3d;
      display: block;
    }}
    .person-name {{
      font-weight: 600;
      font-size: 0.95rem;
      margin-bottom: 4px;
    }}
    .person-count {{
      font-size: 0.8rem;
      color: var(--text-muted);
    }}
    
    /* Homework Cleanup Banner */
    .cleanup-banner {{
      background: rgba(239, 68, 68, 0.1);
      border: 1px solid rgba(239, 68, 68, 0.3);
      border-radius: 10px;
      padding: 16px 20px;
      margin-bottom: 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .btn-action {{
      background: var(--danger);
      color: white;
      border: none;
      padding: 10px 18px;
      border-radius: 8px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s;
    }}
    .btn-action:hover {{ background: var(--danger-hover); }}
    
    /* Lightbox Modal */
    .lightbox {{
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0,0,0,0.9);
      z-index: 100;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 40px;
    }}
    .lightbox.active {{ display: flex; }}
    .lightbox-content {{
      display: flex;
      max-width: 1200px;
      max-height: 90vh;
      background: var(--card-bg);
      border-radius: 16px;
      overflow: hidden;
      border: 1px solid var(--border);
    }}
    .lightbox-img {{
      max-height: 90vh;
      max-width: 65vw;
      object-fit: contain;
      background: #000;
    }}
    .lightbox-sidebar {{
      width: 380px;
      padding: 24px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}
    .close-btn {{
      position: absolute;
      top: 20px;
      right: 24px;
      color: #fff;
      font-size: 2rem;
      cursor: pointer;
    }}
  </style>
</head>
<body>

  <header>
    <div class="logo">
      🏛️ Personal Museum Archive
      <span class="badge">Offline v6</span>
    </div>
    <div class="metrics">
      <div class="metric-pill">📷 <strong>{total_photos:,}</strong> Photos</div>
      <div class="metric-pill">🌍 <strong>{geocoded_count:,}</strong> Geocoded</div>
      <div class="metric-pill">👥 <strong>{cluster_count}</strong> People Clusters</div>
      <div class="metric-pill">📝 <strong>{homework_count:,}</strong> Docs/Homework</div>
    </div>
  </header>

  <nav class="tabs">
    <button class="tab-btn active" onclick="switchTab('gallery')">🖼️ Gallery & Timeline</button>
    <button class="tab-btn" onclick="switchTab('map')">🌍 World Map ({len(locations)} Cities)</button>
    <button class="tab-btn" onclick="switchTab('people')">👥 People & Faces (9 Core Members)</button>
    <button class="tab-btn" onclick="switchTab('homework')">
      📝 Homework Cleanup <span class="tab-badge">{homework_count:,}</span>
    </button>
  </nav>

  <main>
    <!-- Tab 1: Gallery -->
    <div id="tab-gallery" class="tab-pane active">
      <div class="search-box">
        <input type="text" id="searchInput" class="search-input" placeholder="🔍 Search captions, descriptions, tags, cities, or cameras (e.g. 'park', 'zurich', 'iphone 14 pro', 'birthday')..." oninput="filterPhotos()">
      </div>
      <div class="photo-grid" id="photoGrid">
        <!-- Rendered by JS -->
      </div>
    </div>

    <!-- Tab 2: Map -->
    <div id="tab-map" class="tab-pane" style="padding: 16px;">
      <div id="map"></div>
    </div>

    <!-- Tab 3: People & Faces -->
    <div id="tab-people" class="tab-pane">
      <div class="people-section-title">
        👑 Core Family Members (Top 9 Ranked by Frequency)
      </div>
      <div class="people-grid" id="corePeopleGrid">
        <!-- Rendered by JS -->
      </div>

      <div class="people-section-title" style="margin-top: 32px;">
        🤝 Extended Family, Friends & Travel Companions
      </div>
      <p style="color: var(--text-muted); font-size: 0.9rem; margin-bottom: 16px;">
        Open-ended DBSCAN clustering discovered recurring identity clusters while discarding background strangers and tourists as unassigned noise.
      </p>
      <div class="people-grid" id="extendedPeopleGrid">
        <!-- Rendered by JS -->
      </div>
    </div>

    <!-- Tab 4: Homework Cleanup -->
    <div id="tab-homework" class="tab-pane">
      <div class="cleanup-banner">
        <div>
          <h3 style="color: #f87171; margin-bottom: 4px;">School Notebooks, Textbooks & Documents</h3>
          <p style="color: var(--text-muted); font-size: 0.85rem;">
            These {homework_count:,} photos were automatically detected as handwritten math, notebooks, study sheets, and screens.
          </p>
        </div>
        <button class="btn-action" onclick="alert('Exporting cleanup list of all flagged documents...')">
          📥 Export Batch Deletion Script
        </button>
      </div>
      <div class="photo-grid" id="homeworkGrid">
        <!-- Rendered by JS -->
      </div>
    </div>
  </main>

  <!-- Lightbox Modal -->
  <div class="lightbox" id="lightbox" onclick="closeLightbox(event)">
    <span class="close-btn" onclick="closeLightbox()">&times;</span>
    <div class="lightbox-content" onclick="event.stopPropagation()">
      <img id="lightboxImg" class="lightbox-img" src="" alt="Full view">
      <div class="lightbox-sidebar">
        <h3 id="lbTitle" style="font-size: 1.1rem; line-height: 1.4;">Photo Title</h3>
        <p id="lbMeta" style="color: var(--text-muted); font-size: 0.85rem;">Date • Location</p>
        <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); padding: 12px; border-radius: 8px;">
          <h4 style="font-size: 0.85rem; color: #60a5fa; margin-bottom: 6px;">Vision AI Description</h4>
          <p id="lbDesc" style="font-size: 0.85rem; line-height: 1.5; color: var(--text);"></p>
        </div>
        <div style="margin-top: auto;">
          <p id="lbPath" style="font-size: 0.75rem; color: var(--text-muted); font-family: monospace; word-break: break-all;"></p>
        </div>
      </div>
    </div>
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const samplePhotos = {json.dumps(sample_photos)};
    const mapLocations = {json.dumps(locations)};
    const coreClusters = {json.dumps(top_clusters)};

    function switchTab(tabId) {{
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      
      event.target.classList.add('active');
      document.getElementById('tab-' + tabId).classList.add('active');

      if (tabId === 'map' && !window.mapInitialized) {{
        initMap();
      }}
    }}

    function renderGallery(photos, containerId) {{
      const container = document.getElementById(containerId);
      container.innerHTML = photos.map(p => `
        <div class="photo-card" onclick="openLightbox('${{p.id}}')">
          <img class="card-thumb" src="${{p.path}}" alt="${{p.caption}}" loading="lazy">
          <div class="card-info">
            <div class="card-title">${{p.caption || p.id}}</div>
            <div class="card-meta">
              <span>${{p.date}}</span>
              <span>${{p.loc}}</span>
            </div>
          </div>
        </div>
      `).join('');
    }}

    function renderPeople() {{
      const coreGrid = document.getElementById('corePeopleGrid');
      coreGrid.innerHTML = coreClusters.map(c => `
        <div class="person-card">
          <img class="person-avatar" src="avatars/cluster_${{c.id}}.jpg" onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'96\\' height=\\'96\\'><rect width=\\'96\\' height=\\'96\\' fill=\\'%23334155\\'/><text x=\\'50%\\' y=\\'55%\\' dominant-baseline=\\'middle\\' text-anchor=\\'middle\\' fill=\\'white\\' font-size=\\'32\\'>👤</text></svg>'">
          <div class="person-name">${{c.name}}</div>
          <div class="person-count">${{c.count.toLocaleString()}} photos</div>
        </div>
      `).join('');
    }}

    function initMap() {{
      window.mapInitialized = true;
      const streets = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
        attribution: '&copy; Esri &mdash; Street Map',
        maxZoom: 19
      }});
      const satellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
        attribution: '&copy; Esri &mdash; Satellite Imagery',
        maxZoom: 19
      }});
      const topo = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
        attribution: '&copy; Esri &mdash; Topographic',
        maxZoom: 19
      }});
      const map = L.map('map', {{
        center: [25.0, 30.0],
        zoom: 3,
        layers: [streets]
      }});
      L.control.layers({{
        "🗺️ Street Map": streets,
        "🛰️ Satellite Imagery": satellite,
        "⛰️ Topographic": topo
      }}, null, {{ position: 'topright' }}).addTo(map);

      mapLocations.forEach(loc => {{
        const marker = L.circleMarker([loc.lat, loc.lon], {{
          radius: Math.min(24, Math.max(8, Math.log2(loc.count) * 2.5)),
          fillColor: "#3b82f6",
          color: "#ffffff",
          weight: 2,
          opacity: 1,
          fillOpacity: 0.85
        }}).addTo(map);

        marker.bindPopup(`
          <div style="font-family: sans-serif; font-size: 13px;">
            <strong style="font-size: 14px;">${{loc.city}}, ${{loc.country}}</strong><br>
            <span style="color: #64748b;">${{loc.count.toLocaleString()}} photos taken here</span>
          </div>
        `);
      }});
      setTimeout(() => map.invalidateSize(), 200);
    }}

    function filterPhotos() {{
      const q = document.getElementById('searchInput').value.toLowerCase();
      const filtered = samplePhotos.filter(p => 
        p.caption.toLowerCase().includes(q) || 
        p.loc.toLowerCase().includes(q) ||
        p.date.includes(q)
      );
      renderGallery(filtered, 'photoGrid');
    }}

    function openLightbox(id) {{
      const p = samplePhotos.find(x => x.id === id);
      if (!p) return;
      document.getElementById('lightboxImg').src = p.path;
      document.getElementById('lbTitle').innerText = p.caption || p.id;
      document.getElementById('lbMeta').innerText = `${{p.date}} • ${{p.loc}}`;
      document.getElementById('lbDesc').innerText = p.caption;
      document.getElementById('lbPath').innerText = p.path;
      document.getElementById('lightbox').classList.add('active');
    }}

    function closeLightbox(e) {{
      if (!e || e.target.id === 'lightbox' || e.target.classList.contains('close-btn')) {{
        document.getElementById('lightbox').classList.remove('active');
      }}
    }}

    // Initial render
    renderGallery(samplePhotos.filter(p => !p.is_hw), 'photoGrid');
    renderGallery(samplePhotos.filter(p => p.is_hw), 'homeworkGrid');
    renderPeople();
  </script>
</body>
</html>
"""
    with open(viewer_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
        
    size_mb = viewer_path.stat().st_size / (1024 * 1024)
    print(f"✓ Offline Museum Viewer generated successfully: {viewer_path} ({size_mb:.2f} MB).")
    return viewer_path

def purge_processing_cache():
    print(f"Purging temporary processing cache at {PROCESSING_CACHE_DIR}...")
    if PROCESSING_CACHE_DIR.exists():
        file_count = 0
        reclaimed_bytes = 0
        for f in PROCESSING_CACHE_DIR.glob('*.jpg'):
            reclaimed_bytes += f.stat().st_size
            f.unlink()
            file_count += 1
            
        mb = reclaimed_bytes / (1024 * 1024)
        print(f"✓ Purged {file_count:,} temporary proxy files, reclaimed {mb:.1f} MB of disk space.")
    else:
        print("Processing cache does not exist or was already cleared.")

def run_stage5():
    print("=" * 60)
    print("STAGE 5: FTS5 SEARCH, CATALOG EXPORT & MUSEUM VIEWER")
    print("=" * 60)
    
    conn = get_connection()
    build_fts5_index(conn)
    export_catalog_json(conn)
    generate_viewer_html(conn)
    conn.close()
    
    # Export full 32,424-item catalog_data.js for browser viewer
    export_catalog_data_js()
    
    if '--purge-cache' in sys.argv:
        purge_processing_cache()

if __name__ == '__main__':
    run_stage5()
