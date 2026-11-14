# Local Media-Processing Pipeline: Stage 4 & 5 Handoff Guide

**Workspace Root**: `<archive_root>` (locally placed in gitignored `Memory_Setup/archive/`)  
**Universal Project Workspace**: `Memory_Setup/` (`https://github.com/Nikunj-Gupta-1/memory-setup`)  
**Current Date**: November 12, 2026  
**Environment**: Apple M2 Pro, 16 GB Unified Memory, macOS 27.2, Python 3.15.2, Ollama running locally.  
**Available Disk Space**: 36 GiB (well above the 15.0 GiB safety limit).

---

## 1. Executive Summary & Current State

Stages 1, 2, 3, Stage 4 Face Recognition & Clustering, and Stage 5 Museum Viewer are **100% completed, verified, and operational**.
The user can immediately open and browse their entire 18-year archive in [`viewer.html`](viewer.html).

### Completed Milestones:
1. **Stage 1 (Consolidation & Direct Move)**:
   - 33,771 master files consolidated into `images/YYYY/` (32,424 photos) and `videos/YYYY/` (1,337 videos).
   - 39 companion sets (78 files: Live Photos MOV, `.aae` sidecars) linked via `companion_group_id`.
   - Zero storage overhead via direct APFS moves. Old source directories cleared.

2. **Stage 2 (Metadata & Standardized Proxies)**:
   - Complete technical EXIF/QuickTime metadata extracted into `metadata_exif`.
   - 20,608 photos (63.6%) reverse-geocoded offline to City, State, and Country across 35 major world destinations.
   - Refreshed 14,679 proxy JPEGs in `.processing_cache/` (841 MB) via unsandboxed multi-threaded `sips`.

3. **Stage 3 (Validation & AI Manifest)**:
   - 100% of images validated as decodable without corruption.
   - 64-bit dHash computed for all photos; 98 undated photos matched to dated originals.
   - Immutable AI manifest exported to `catalog.sqlite`, `ai_manifest.json`, and `ai_manifest.csv`.

4. **Stage 4 (Hardware Face Recognition & BLAS Clustering)**:
   - Apple Neural Engine scanned all 32,424 images in 5.2 minutes (103.6 img/s) via native Swift binary `bin_face_extractor`.
   - 43,119 faces detected and feature-printed across 15,923 photos into `people_faces`.
   - Pre-allocated NumPy BLAS Leader Centroid Clustering grouped all 43,119 vectors in **1.37 seconds**.
   - Discarded 3,299 stranger/tourist faces as noise (`cluster_id = -1`).
   - Isolated and ranked the **Top 9 Core Family Members** (`cluster_id = 0..8`, 450 to 736 photos each).
   - Generated 30 avatar thumbnails in `avatars/`.

5. **Stage 5 (Search, Dual Export & Offline Museum Viewer)**:
   - Populated SQLite `ai_search_fts` virtual table (32,424 rows).
   - Exported full hierarchical `catalog.json` (27.75 MB).
   - Generated compact `catalog_data.js` (6.56 MB) for instant browser DOM rendering.
   - Built single-file offline museum [`viewer.html`](viewer.html) (26.5 KB) featuring:
     - 48-item virtualized lazy-loading gallery.
     - Keyless Esri ArcGIS Online World Map (Streets, Satellite, Dark Canvas, Topographic) with city pins and popups.
     - People & Faces tab with avatar cards for the 9 core family members.
     - Homework & Documents cleanup tab with batch-deletion script exporter.
     - Full lightbox modal with technical EXIF sidebar.
   - Handled browser HEIC incompatibility using `.processing_cache/` JPEG fallbacks.

---

## 2. Directory Layout & Architecture

```
<archive_root>/
├── images/                             # 32,424 master photos partitioned by year
├── videos/                             # 1,337 master videos partitioned by year
├── excluded/                           # Preserved non-media items (.doc files)
├── .processing_cache/                  # 14,679 proxy JPEGs (841 MB; keep until full vision pass completes)
├── avatars/                            # 30 face avatars for Top 9 clusters (cluster_0.jpg to cluster_8.jpg)
├── reports/                            # Verified stage reports (stage1 through stage5)
├── pipeline/                           # Modular pipeline scripts
│   ├── config.py                       # Paths and database connection
│   ├── db.py                           # SQLite schema and helpers
│   ├── geocoder.py                     # Offline reverse geocoding engine
│   ├── stage1_consolidate.py
│   ├── stage2_standardize.py
│   ├── stage3_validate.py
│   ├── face_extractor.swift            # Native Apple Vision Swift source
│   ├── stage4_clustering.py            # NumPy BLAS Leader Centroid clustering
│   ├── stage4_vision.py                # Resumable Ollama vision processor
│   ├── stage5_museum.py                # FTS5 search & viewer generator
│   ├── refresh_proxies.py              # Proxy HEIC re-conversion
│   └── verify_stage.py                 # Automated verification harness
├── catalog.sqlite                      # Production SQLite database (235 MB)
├── ai_manifest.json                    # Deterministic AI manifest (25.08 MB)
├── catalog.json                        # Master archive export (27.75 MB)
├── catalog_data.js                     # Browser data payload (6.56 MB)
├── viewer.html                         # Offline Museum Viewer web application
├── MASTER_PROGRESS_REPORT.md           # Master exhaustive report
└── STAGE4_HANDOFF.md                   # This handoff guide
```

---

## 3. Important Execution Guidelines for the Next AI

1. **Sandbox Execution Rule**:
   * Always pass `BypassSandbox: true` for any terminal commands involving Ollama, sips, Swift compiler, Python scripts, or file edits.

2. **Preserve `.processing_cache/`**:
   * **Do NOT purge `.processing_cache/` yet**. The JPEG proxies inside it are actively used by `viewer.html` to display HEIC photos in web browsers, and are needed for the upcoming full vision analysis pass.

3. **Pending Tasks**:
   * **Full Stage 4 Vision Pass (Run Overnight / Off-Hours)**:
     ```bash
     cd <archive_root>
     PYTHONPATH=. python3 pipeline/stage4_vision.py
     ```
   * **Post-Vision Re-export & Optional Cache Purge**:
     ```bash
     PYTHONPATH=. python3 pipeline/stage5_museum.py --purge-cache
     ```
   * **Face Recognition Quality Upgrade**:
     Implement InsightFace / ArcFace with 5-point facial landmark alignment, or add an interactive "Merge Clusters" button in `viewer.html` to consolidate childhood vs. adulthood sub-clusters.
   * **Universal Autonomous Pipeline (`Memory_Setup`)**:
     Build the mass-producible CLI tool in `Memory_Setup/` per `standalone_app_implementation_plan.md`.
