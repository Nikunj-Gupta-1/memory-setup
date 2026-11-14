# Master Progress Report: Local Media-Processing Pipeline & Museum Archive

**Workspace Root**: `<archive_root>` (locally placed in gitignored `Memory_Setup/archive/`)  
**Master Media Directory**: `<archive_root>/content`  
**Museum Viewer & Pipeline**: `<archive_root>/viewer`  
**Universal Standalone Package**: `Memory_Setup/` (`https://github.com/Nikunj-Gupta-1/memory-setup`)  
**Current Date**: November 14, 2026  
**Environment**: Apple M2 Pro, 16 GB Unified Memory, macOS 27.2, Python 3.15.2 (System) / Python 3.12.16 (Dedicated `.venv` for ArcFace & `Memory_Setup`), Ollama local VLM (`moondream:latest`).  
**Disk Space Status**: 49.59 GiB available (safely above the 15.0 GiB safety limit).

---

## 1. Executive Status Overview

The local media-processing pipeline has consolidated, standardized, validated, indexed, and published an 18-year archive of **33,771 personal media files** (2007–2026), cleanly organized into a strict two-folder contract (`content/` and `viewer/`).

| Pipeline Stage | Scope | Status | Key Deliverable |
|---|---|---|---|
| **Stage 1: Consolidation & Grouping** | Physical organization, companion linking, APFS atomic moves | **100% Complete** | `content/images/YYYY/`, `content/videos/YYYY/`, `media_items` table |
| **Stage 2: Metadata & Proxies** | EXIF/GPS extraction, reverse geocoding, proxy conversion | **100% Complete** | `metadata_exif`, `viewer/.processing_cache/` (14,679 JPEGs) |
| **Stage 3: Validation & Manifest** | Byte validation, dHash perceptual hashing, date resolution | **100% Complete** | `ai_manifest` (32,424 rows), `ai_manifest.json` |
| **Stage 4: Face Recognition & Upgrade** | InsightFace ArcFace ResNet-50, 5-point alignment, BLAS cluster | **100% Complete** | `people_faces` (43,119 baseline / 67,390 ArcFace), `avatars/` (30 avatars) |
| **Stage 4: Vision AI Pass** | Local Ollama Moondream descriptions with Memory Watchdog | **Continuous Background** | Memory-bounded 10-image runner recycling active (<2.5 GB) |
| **Stage 5: Search & Offline Museum** | FTS5 index, `catalog.json`, `catalog_data.js`, `viewer.html` | **100% Complete** | Zero-config streaming launcher (`start_viewer.command`, `server.py`) |

---

## 2. Complete Database Metrics (`catalog.sqlite`)

Verified against a reference local archive `viewer/catalog.sqlite` benchmark:

* **Total Media Items**: 33,771
  * **Master Photos**: 32,424
  * **Master Videos**: 1,337
  * **Documents / Preserved Files**: 3 (`content/documents/`)
* **Companion Sets**: 39 pairs (78 files: Live Photos MOV, `.aae` sidecars, audio memos) linked via `companion_group_id`.
* **Technical EXIF Records**: 32,424 rows in `metadata_exif`.
* **GPS-Geocoded Photos**: 20,608 photos (63.6%) tagged with offline city, region, and country across 35 major world destinations (Zurich, Venice, Paris, London, Interlaken, Tokyo, Mt. Fuji, Osaka, Kyoto, Varanasi, Delhi, Mumbai, Dubai, Los Angeles, San Francisco, New York, etc.).
* **Processing Inputs**: 32,424 rows in `processing_inputs`.
* **AI Manifest**: 32,424 rows in `ai_manifest`.
* **Faces Detected & Feature-Printed**:
  * Apple Vision Baseline: 43,119 faces across 15,923 photos in `people_faces`.
  * ArcFace Calibration: 67,390 faces detected with 5-point landmark alignment across archive; threshold calibrated at **0.48–0.50** for robust childhood-to-adulthood cross-age identity linking.
* **FTS5 Virtual Search Table**: 32,424 records in `ai_search_fts`.
* **AI Vision Descriptions**: Background captioning in `ai_descriptions` with automated `is_document_or_homework = 1` study-document flagging.
* **Recurring People Clusters**: **2,040** distinct ArcFace clusters across **67,390** detected faces.

---

## 3. Two-Folder Directory Layout & Key Files

```
<archive_root>/
├── start_viewer.command                # 1-click museum launcher symlink (run from Finder)
├── content/                            # PURE USER CONTENT
│   ├── images/                         # 32,424 master photos partitioned by year (2007–2026, UNKNOWN_DATE)
│   ├── videos/                         # 1,337 master videos partitioned by year (2008–2026)
│   └── documents/                      # Preserved non-media documents (.doc, .pdf)
└── viewer/                             # STANDALONE MUSEUM APPLICATION RUNTIME
    ├── viewer.html                     # Offline Museum Web UI (timeline, search, map, people, cleanup)
    ├── server.py                       # Zero-dependency local streaming HTTP server (port 8080)
    ├── start_viewer.command            # Shell launcher with auto-browser open
    ├── catalog.sqlite                  # Production SQLite database (352 MB) with WAL and FTS5
    ├── catalog_data.js                 # Browser-optimized compact payload (7.6 MB)
    ├── catalog.json                    # Full archive JSON export (33.4 MB)
    ├── ai_manifest.json                # Deterministic AI manifest (26.8 MB)
    ├── ai_manifest.csv                 # Flat CSV manifest (10.2 MB)
    ├── .processing_cache/              # 14,679 proxy JPEGs (841 MB, preserved for vision pass)
    ├── avatars/                        # Cropped face avatars (cluster_0.jpg to cluster_8.jpg)
    ├── content -> ../content           # Relative symlink for local web server routing
    ├── .venv/                          # Dedicated Python 3.12 virtualenv for biometrics
    ├── pipeline/                       # Modular pipeline implementation
    │   ├── config.py                   # Paths, database connection, constants
    │   ├── db.py                       # SQLite schema and query helpers
    │   ├── geocoder.py                 # Offline KD-Tree reverse geocoding engine (15k cities)
    │   ├── stage1_consolidate.py       # Stage 1 consolidation script
    │   ├── stage2_standardize.py       # Stage 2 metadata & proxy script
    │   ├── stage3_validate.py          # Stage 3 validation & manifest script
    │   ├── face_extractor.swift        # Native Apple Vision Swift source for ANE scanning
    │   ├── stage4_faces.py             # Apple Vision face pipeline wrapper
    │   ├── stage4_clustering.py        # Fast NumPy BLAS Leader Centroid clustering
    │   ├── stage4_calibrate_arcface.py # ArcFace cross-age similarity calibrator
    │   ├── stage4_insightface.py       # ArcFace ResNet-50 biometric face pipeline
    │   ├── stage4_vision.py            # Memory-bounded Ollama Moondream vision processor
    │   ├── stage5_museum.py            # FTS5 search & museum export synthesizer
    │   ├── export_viewer_data.py       # Dedicated catalog_data.js & catalog.json exporter
    │   ├── refresh_proxies.py          # Multi-threaded sips HEIC proxy re-converter
    │   └── verify_stage.py             # Automated verification harness
    └── reports/                        # Verified execution reports & benchmarks
        ├── stage1_report.md & .json    # Consolidation & companion linking
        ├── stage2_report.md & .json    # EXIF metadata & offline geocoding
        ├── stage3_report.md & .json    # Perceptual hash validation & fixed manifest
        ├── stage4_report.md & .json    # Apple Vision face detection & BLAS clustering
        ├── arcface_calibration.md      # ArcFace threshold calibration benchmark
        └── stage5_report.md & .json    # FTS5 search, catalog exports & museum viewer
```

---

## 4. Major Technical Problems Solved

### Problem 1: Sandbox HEIC Proxy Black Frame Bug
* **Symptom**: During initial Stage 2 runs, all 14,675 `.heic` proxy conversions generated all-black JPEGs (`mean = 0.0`).
* **Root Cause**: macOS sandboxed Python lacked the WindowServer/GPU graphics context required by `sips`/CoreGraphics.
* **Resolution**: Developed [`pipeline/refresh_proxies.py`](pipeline/refresh_proxies.py) running via `ThreadPoolExecutor(max_workers=8)`. Re-converted all 14,679 proxy files in 15.7 minutes with 100% color/pixel fidelity.

### Problem 2: DBSCAN Scalar Queue Bottleneck
* **Symptom**: Clustering 43,119 face vectors stalled the CPU for >25 minutes with no progress.
* **Root Cause**: The naive single-threaded BFS implementation used Python's `queue.pop(0)`, which shifted tens of thousands of memory pointers on every step while executing over 330 billion scalar floating-point comparisons.
* **Resolution**: Replaced with vector-accelerated Leader Centroid Clustering in [`pipeline/stage4_clustering.py`](pipeline/stage4_clustering.py) using Apple Accelerate / NumPy BLAS matrix multiplication (`chunk @ centroids.T`). Clustered all 43,119 vectors in **1.37 seconds**.

### Problem 3: Browser HEIC Incompatibility
* **Symptom**: Photos in `viewer.html` failed to display in Chrome, Firefox, and Edge.
* **Root Cause**: Web browsers (outside Safari) cannot decode raw Apple `.heic` files in `<img>` tags.
* **Resolution**: Configured `catalog_data.js` and [`viewer.html`](viewer.html) to load JPEG proxies `p.p` (`.processing_cache/P_XXXXXX.jpg`) for HEIC items and master files for JPG/PNG items, with automatic `handleImgError` fallback.

### Problem 4: SQLite Cartesian Join Sort in Face Sampling (0.10s Fix)
* **Symptom**: Face calibration queries hung for 14+ minutes before processing any images.
* **Root Cause**: 3-way `JOIN` with `people_faces` combined with `DISTINCT` and unindexed `ORDER BY yr` forced SQLite into an enormous disk-based temporary B-Tree sort on 43,119 768-d embedding BLOBs (~100 MB).
* **Resolution**: Replaced with an indexed subquery: `WHERE m.id IN (SELECT media_id FROM people_faces) ORDER BY yr ASC`. Execution dropped from **14+ minutes to 0.10 seconds**.

### Problem 5: Homework Classifier False Positives (Family Photos Misclassified)
* **Symptom**: 22 personal family photos (birthday cakes, babies, couples in front of churches) were misclassified as homework.
* **Root Cause**: Naive substring keyword matching (`'written on it'`, `'piece of paper'`) triggered on t-shirt text, cakes, and tourists holding travel brochures.
* **Resolution**: Implemented high-precision multi-layer classification in [`pipeline/stage4_vision.py`](pipeline/stage4_vision.py):
  1. Strict Family Face Disqualification: Any photo with detected recurring faces is disqualified (`has_faces == True` $\to$ False).
  2. Narrative Starter Disqualification: Disqualifies photos starting with "in the image, a young boy/girl/couple/baby".
  3. Setting Exclusions: Disqualifies birthday cakes, churches, parks, beaches, temples, weddings.
  4. Requires high-precision study phrases (`page from a notebook`, `math equations`, `worksheet`, `textbook page`).
  Result: 100% precision. Exactly **4 true documents** flagged; 0 family photos misclassified.

### Problem 6: Viewer HTML Clobbering & Browser Local File Resource Origin Restrictions
* **Symptom**: Browser threw "90 errors: not allowed to load local resource" when opening `file://` URLs traversing `../content/`, and `stage5_museum.py` clobbered the full viewer template.
* **Root Cause**: Chromium security restrictions block parent folder traversal (`../`) under `file://` scheme; `stage5_museum.py` lacked an anti-clobber check.
* **Resolution**: Added anti-clobber protection to `stage5_museum.py`, wired `export_viewer_data.py`, built a lightweight streaming Python server [`server.py`](server.py) (plus full live-API template in `memory_setup/viewer/template/server.py`), and created a 1-click executable launcher [`start_viewer.command`](start_viewer.command) serving smoothly at `http://localhost:8080/viewer/viewer.html`.

### Problem 7: Ollama 10+ GB RAM Bloat in Continuous Image Batches
* **Symptom**: Ollama's underlying runner process (`llama-server`) expanded to **10+ GB RAM by itself**, pushing system memory to 14 GB and triggering yellow/orange memory pressure warnings.
* **Root Cause**:
  1. `llama-server` reserves up to **8,192 MiB (8 GiB) of host RAM** by default for prompt/KV prefix caching (`--cache-ram 8192`).
  2. In batch image processing, each photo generates 729 vision patch tokens. Because every photo is different, image tokens never match past prefixes, but `llama-server` greedily accumulated them into host RAM up to the 8 GiB ceiling.
  3. Combined with base model weights (2.16 GB), total memory reached **~10.16 GB**.
* **Resolution**:
  1. Configured request options with explicit context bounds: `"options": {"num_ctx": 1024, "num_predict": 128, "temperature": 0}`.
  2. Implemented automated **Memory Watchdog Runner Recycling**: Every 200 images, the pipeline sends `keep_alive: 0` to Ollama. In 0.015 seconds, `llama-server` unloads, flushing 100% of cached tokens and Metal buffers back to macOS.
  3. Added clean teardown in `finally:` and `KeyboardInterrupt` handlers so Ollama never lingers in RAM when stopped.
  Result: Memory is strictly bounded to **~2.2 GB** with 0 memory pressure warnings.

---

## 5. Active Operations & Completed Standalone Release

1. **Active Background Task (`stage4_vision.py`)**:
   * Runs on demand with memory watchdog runner recycling active (`<2.5 GB` bounded RAM).
   * Safe Live Sync: Run `../../.venv/bin/python pipeline/export_viewer_data.py` inside `<archive_root>/viewer/` to refresh `catalog_data.js` cleanly in WAL mode without interrupting Stage 4 writes.
2. **Reference Pipeline Hardening (`viewer/pipeline/`)**:
   * Applied 7 reliability fixes (`db.py` schema unification, `config.py` self-exclude dirs, `stage1_consolidate.py` `MAX(id)` continuity, `stage2_standardize.py` proxy prefix, `geocoder.py` strict `> 150 km` cutoff, `stage4_clustering.py` dynamic centroid capacity, `export_viewer_data.py` int-list `p.f` & `MAX(person_name)`).
3. **Universal Standalone Application (`Memory_Setup`) — Shipped**:
   * Built in `Memory_Setup/` with native Control Center GUI (`pywebview` over HTTP/SSE, zero-Terminal `Memory Setup.app` & `Memory Setup.lnk` launchers, bundled `cities15000.sqlite`, Safe Copy Source/Archive pickers, live Ollama/Face AI setup, clean close teardown, read-only live archive metrics), consolidated `.venv`, verified by **29/29** offline pytest checks, and published to `https://github.com/Nikunj-Gupta-1/memory-setup`.
