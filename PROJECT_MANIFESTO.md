# The Media Intelligence Engine & Offline Museum: Project Manifesto

**Date**: November 12, 2026  
**Local Archive Layout**: Strict two-folder contract (`content/` + `viewer/`, placed in local gitignored `archive/` during development)  
**Standalone Package Repo**: `Memory_Setup/` (`https://github.com/Nikunj-Gupta-1/memory-setup`)  
**Host Platform**: Apple Silicon macOS (Apple M2 Pro 10c/16g/16ane, 16 GB Unified Memory, APFS SSD)  

---

## 1. Executive Summary & The Vision

This project originated with a massive, chaotic collection of **33,771 personal files** (photos, videos, Live Photo companions, sidecars, and random documents) accumulated across 18 years (2007–2026). The user's primary objectives were:

1. **Cleanly Organize & Normalize**: Transform the chaotic folder into a clean, permanent, standardized archive without data loss or duplicates.
2. **Strict Two-Folder Contract**:
   * **`content/`**: Pure master user media (`images/YYYY/`, `videos/YYYY/`, and `documents/`).
   * **`viewer/`**: Complete self-contained museum web application runtime (`viewer.html`, `catalog.sqlite`, face avatars, proxies, and streaming server).
3. **Local-First Intelligence**: Detect faces, extract 5-point landmark biometrics, cluster core family members across aging drift (childhood to adulthood), geocode coordinates to real-world cities offline, describe scenes using local Vision-Language Models (VLM), and flag homework/study documents for cleanup—all at **zero cloud API cost** and with **zero privacy leakage**.
4. **Universal Standalone Application**: Package this engine into an autonomous, zero-config desktop application (`memory_setup/`) featuring an interactive setup wizard, multiple execution modes (In-Place Reorganization vs Safe Copy), and memory-bounded stability.

---

## 2. Chronological Milestones & Architecture Evolution

### Milestone 1: Stage 1 Consolidation & Companion Linking
* **Challenge**: 33,771 items scattered across deeply nested subfolders, with Apple Live Photos (`.mov`), `.aae` sidecars, and mixed naming schemes.
* **Resolution**: Implemented [`pipeline/stage1_consolidate.py`](reference_pipeline/viewer/pipeline/stage1_consolidate.py). Used APFS atomic pointer re-parenting (`os.rename`) to partition files into `images/YYYY/` and `videos/YYYY/` in **6.8 minutes** with **0 bytes of extra disk consumed**.
* **Companion Pairing**: Grouped 39 pairs (78 files) of Apple Live Photos and sidecars under unique `companion_group_id`s in `catalog.sqlite`.

### Milestone 2: Stage 2 Metadata, Offline Geocoding & Proxy Generation
* **Challenge**: Extract EXIF/QuickTime timestamps and reverse-geocode coordinates without network API keys. Furthermore, macOS web browsers cannot decode `.heic` images directly.
* **Resolution**:
  * Implemented [`pipeline/stage2_standardize.py`](reference_pipeline/viewer/pipeline/stage2_standardize.py) with an offline KD-Tree spatial index ([`pipeline/geocoder.py`](reference_pipeline/viewer/pipeline/geocoder.py)) containing 15,000 world cities. Reverse-geocoded 20,608 photos (63.6%) across 35 major world destinations in 4.2 minutes (0.05ms/photo).
  * Resolved the "Sandbox Black Frame" bug by deploying multi-threaded `sips` in [`pipeline/refresh_proxies.py`](reference_pipeline/viewer/pipeline/refresh_proxies.py), generating 14,679 proxy color JPEGs in `.processing_cache/` (841 MB) at 15.6 img/s.

### Milestone 3: Stage 3 Media Validation & Deterministic AI Manifest
* **Challenge**: Guarantee byte-level media integrity, compute perceptual hashes (dHash) to resolve undated photos, and export a canonical manifest.
* **Resolution**: Implemented [`pipeline/stage3_validate.py`](reference_pipeline/viewer/pipeline/stage3_validate.py). Validated 32,424 master photos and generated `ai_manifest.json` (26.8 MB) and `ai_manifest.csv` (10.2 MB).

### Milestone 4A: Apple Neural Engine Face Scanning & Fast BLAS Clustering
* **Challenge**: Traditional Python face detection models were too slow for 32,000 photos, and naive DBSCAN clustering stalled for >25 minutes.
* **Resolution**:
  * Built a native Swift CLI binary ([`face_extractor.swift`](reference_pipeline/viewer/pipeline/face_extractor.swift)) utilizing Apple Vision on the 16-core Apple Neural Engine (ANE). Scanned all 32,424 images in **5.2 minutes (103.6 img/s)**, detecting 43,119 faces.
  * Replaced scalar queue clustering with vectorized Leader Centroid Clustering in [`pipeline/stage4_clustering.py`](reference_pipeline/viewer/pipeline/stage4_clustering.py) via NumPy / Apple Accelerate BLAS matrix multiplication (`chunk @ centroids.T`). Clustered all 43,119 vectors in **1.37 seconds (31,473 vectors/s)**, isolating the Top 9 core family members.

### Milestone 4 Upgrade: ArcFace ResNet-50 5-Point Landmark Calibration
* **Challenge**: Apple Vision embeddings lack facial alignment, splitting childhood photos from adulthood photos of the same individual.
* **Resolution**:
  * Upgraded face pipeline to **InsightFace / ArcFace ResNet-50** (`buffalo_l`) in [`pipeline/stage4_insightface.py`](reference_pipeline/viewer/pipeline/stage4_insightface.py) running in the consolidated Python 3.12 virtualenv (`.venv`).
  * Implemented cross-age calibration in [`pipeline/stage4_calibrate_arcface.py`](reference_pipeline/viewer/pipeline/stage4_calibrate_arcface.py).
  * Solved SQLite Cartesian sort bottleneck (reduced candidate sampling query from 14+ minutes to 0.10s via an indexed subquery).
  * Calibrated the optimal cosine similarity threshold at **0.48–0.50**, successfully bridging childhood and adult faces without cross-family false merges. Expanded Top 9 family member coverage from 4.9k to 24.3k faces.

### Milestone 4B: High-Precision Homework & Study Document Detection
* **Challenge**: 22 personal family photos (birthday cakes, babies, couples in front of churches) were misclassified as homework because Moondream mentioned text on clothing or cakes.
* **Resolution**: Upgraded [`pipeline/stage4_vision.py`](reference_pipeline/viewer/pipeline/stage4_vision.py) with a multi-layer classifier:
  1. Strict Family Face Disqualification (`has_faces == True` $\to$ False).
  2. Narrative Subject Disqualification (person/couple/group starters $\to$ False).
  3. Event Setting Exclusions (birthday cakes, churches, parks, beaches, weddings $\to$ False).
  4. High-Precision Study Phrases Only (`page from a notebook`, `math equations`, `worksheet`, `textbook page`).
  * Migrated database: exactly **4 true study documents/screenshots** flagged with 100% precision.

### Milestone 4C: Ollama 10+ GB Memory Diagnosis & Watchdog Recycling
* **Challenge**: Running continuous background vision analysis caused Ollama to expand to 10+ GB of RAM by itself, triggering macOS yellow memory pressure.
* **Root Cause**: Upstream `llama-server` reserves up to 8 GiB of host RAM for prompt/KV caching (`--cache-ram 8192`). In batch image processing where every photo is unique, the cache greedily accumulated 729 vision tokens per image until reaching the 8 GiB ceiling.
* **Resolution**:
  1. Bounded context options: `"options": {"num_ctx": 1024, "num_predict": 128, "temperature": 0}`.
  2. Memory Watchdog Runner Recycling: Every 200 images, the pipeline sends `keep_alive: 0` to Ollama. In 0.015 seconds, `llama-server` unloads and terminates, releasing 100% of accumulated cache and Metal buffers back to macOS before cleanly restarting for the next batch.
  3. Clean exit handlers in `finally:` and `KeyboardInterrupt`. Memory stays locked under **~2.2 GB** with 0 memory pressure.

### Milestone 5: Offline Museum UI, Anti-Clobbering & Streaming Server
* **Challenge**: Modern Chromium browsers block `file://` parent traversal (`../content/`), and pipeline scripts clobbered `viewer.html`.
* **Resolution**:
  * Implemented anti-clobber protection in `stage5_museum.py` and dedicated export in `export_viewer_data.py`.
  * Built [`viewer/server.py`](reference_pipeline/viewer/server.py) (and synced live-API template `memory_setup/viewer/template/server.py`) plus 1-click executable launcher [`start_viewer.command`](reference_pipeline/viewer/start_viewer.command) with relative symlink `viewer/content -> ../content`.
  * Museum UI features: 60fps timeline virtualized gallery, 35-city Leaflet map (Esri keyless tiles), Top 9 family member avatar manager, Lightbox metadata inspector, and Homework cleanup batch exporter.

---

## 3. Database Schema Overview (`viewer/catalog.sqlite`)

The 352 MB SQLite database operates with Write-Ahead Logging (`PRAGMA journal_mode=WAL`) and contains:

1. `media_items`: Master catalog (id, original_path, current_path, filename, ext, mime_type, media_type, file_size_bytes, sha256, companion_group_id, is_companion_primary, capture_timestamp, capture_year, created_at).
2. `metadata_exif`: Technical metadata (make, model, lens, iso, f_number, exposure_time, focal_length, width, height, gps_lat, gps_lon, gps_alt, city, country, region).
3. `processing_inputs`: Mapping of media items to master/proxy files (`processing_path`, `is_proxy`).
4. `ai_manifest`: Byte validation, perceptual hashes (`dhash`), and date sources.
5. `people_faces`: Face bounding boxes, confidence, cluster assignments (`cluster_id`), and 768-d / 512-d feature embeddings.
6. `ai_descriptions`: AI vision captions (`model_name`, `short_caption`, `detailed_description`, `tags`, `scene_type`, `primary_subjects`, `is_document_or_homework`, `described_at`).
7. `ai_search_fts`: SQLite FTS5 virtual table for instantaneous sub-millisecond search across all captions, locations, dates, and tags.

---

## 4. Current State & Completed Standalone Implementation (November 14, 2026)

1. **Local Reference Archive (`stage4_vision.py` in gitignored `archive/`)**:
   * All 7 reference pipeline bugs identified in audit (schema drift, `IGNORE_DIRNAMES` self-scan, proxy path prefixing, `> 150 km` geocoder fallthrough, >15,000 centroid overflow, `p.f` int list serialization, and `MAX(id)` continuity) were patched and syntax-verified.
   * Background Moondream captioning runs single-worker with 10-image runner recycling inside the local gitignored `archive/` workspace.
   * Live Viewer Sync while Stage 4 runs: run `python3 pipeline/export_viewer_data.py` inside `viewer/` (read-only WAL query) rather than competing for SQLite write locks.
2. **Completed Universal Standalone Application (`Memory_Setup`)**:
   * Implemented full `memory-setup` Python package (`config.py`, `db.py`, `setup/`, `ingestion/`, `geocoding/`, `biometrics/`, `vision/`, `viewer/`, `pipeline_runner.py`, `gui/`, `cli.py`).
   * Built 100% native Control Center GUI (`pywebview` over local stdlib `ThreadingHTTPServer` + SSE starting on `127.0.0.1:8765`, bundled `cities15000.sqlite` geocoder) supporting Safe Copy Archive + Source folder pickers, live Ollama start/pull streaming, 1-click Face AI install, Whisper (`50`-item recycle) vs Turbo (`200`-item recycle), cooperative Pause & Flush RAM, **Incremental & Rebuild Options** (`Force Reprocess Existing Media`, `Force Full Face Re-Cluster`), **Manage & Merge People**, **Quarantine Docs**, clean window-close teardown, and live read-only archive metrics polling.
   * **Offline Video Cinema, Read-Only Museum UI & Incremental Re-Runs**: Exported `ALL_VIDEOS` into `catalog_data.js`, added HTTP `206` Byte-Range video streaming and guarded `POST /api/open-media` OS playback (`.avi` legacy fallback) to both `server.py` and the Control Center, overhauled `viewer.html` into a strictly read-only museum with Warm Archival Daylight / Darkroom themes + keyboard lightbox + paginated lazy-loaded Homework review (`60`-card batches), and made Stages 1–4A strictly incremental (`face_scanned` + legacy checkpoint backfill for zero-face photos, size-matched lazy SHA-256 backfill, centroid-preserving face clustering).
   * Added zero-Terminal macOS & Windows launchers (`Memory Setup.app` / `Start_Memory_Setup.command` and `Start_Memory_Setup.bat` -> `Memory Setup.lnk`), verified **36/36** tests passing (`pytest tests -v`), and published cleanly (zero personal data) to `https://github.com/Nikunj-Gupta-1/memory-setup`.
