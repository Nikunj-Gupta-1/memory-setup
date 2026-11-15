# AGENT.MD: Universal Standalone Application Engineering Briefing

> **CRITICAL DIRECTIVE FOR ANY AGENT WORKING ON MEMORY_SETUP**:  
> Read this file, along with [`standalone_app_implementation_plan.md`](standalone_app_implementation_plan.md), before writing code or modifying this repository.

---

## 1. Current Status & Architecture

* **Standalone App (`memory_setup/`)**: **Implemented & Verified** (`44/44` pytest checks passing).
* **Public Repository**: `https://github.com/Nikunj-Gupta-1/memory-setup` (includes `memory_setup/` standalone package, `reference_pipeline/` preserved strictly as historical archive, and `tests/`).
* **Local Personal Archive (Gitignored)**: Any live archive operated alongside this repository lives under gitignored `archive/` (`archive/content/` + `archive/viewer/`) using the unified Python 3.12 `.venv` at the repository root. **Never** stage, commit, or push anything inside `archive/`, `content/`, `*.sqlite*`, `share_config.json`, `.processing_cache/`, `avatars/`, `catalog_data.js`, `catalog.json`, `ai_manifest.*`, or `viewer/content` symlinks.
* **1-Click Launchers**: `Memory Setup.app` / `Start_Memory_Setup.command` (`--rebuild-app` deletes and rebuilds the macOS `.app` bundle + `.venv`) and `Start_Memory_Setup.bat` -> `Memory Setup.lnk` (`--rebuild-app` refreshes `.venv` and regenerates shortcut).

The user contract:
1. Ingest any chaotic folder of photos/videos in any arbitrary nested hierarchy (`sniffer.py` magic-byte header detection + `companions.py` Live Photo / sidecar grouping).
2. 100% Studio GUI Control Center (`http://127.0.0.1:8765`, zero CLI required) featuring a left navigation sidebar menu (**Overview & Run**, **Stage-by-Stage Runner** with individual Deduplication/EXIF/Routing/Face Detection/Fast Re-Cluster/Vision AI/Sync actions, **Curation & People**, **Storage & AI Profile**, **System Readiness**, **Remote Family Share**, and **Show All Panels**), explained action cards, native folder pickers, pre-flight audit, incremental **Force Reprocess** / **Force Full Face Re-Cluster** toggles, **Start Ollama**, live-progress **Pull Moondream**, 1-click **Install Face AI**, **Manage & Merge People** (SQLite rename/merge modal), **Quarantine Docs**, **Remote Family Share** (zero-terminal password + port setup in `share_config.json`, read-only server start/stop, Tailscale status/app launch, 1-click `cloudflared` install + Cloudflare Quick Tunnel start/stop, Tailscale/Cloudflare/LAN URL copy cards, built-in Tailscale & Cloudflare guide), and **Open Offline Museum Viewer**. All archive-mutating actions live exclusively in the Control Center Studio (`viewer.html` is strictly read-only exhibition/playback with a **🔒 Lock** session button).
3. **In-Place Reorganization Mode** (atomic pointer renames, 0 extra disk space) and **Safe Copy Mode** (preserves source originals with dedicated Archive + Source folder pickers).
4. Memory-bounded VLM processing (single-worker `MemoryBoundedOllamaClient`, `cache_prompt: false`, `num_ctx: 1024`, `num_predict: 128`, **Whisper Mode** recycle every `50` items / **Turbo Mode** every `200` items, RSS watchdog).
5. **Consolidated Single-Schema Incremental Operation (`reprocess=False`, `recluster=False`)**: All pipeline stages and Control Center actions read and write one unified `viewer/catalog.sqlite` schema (`media_items` holding `matched_dated_id`/`matched_timestamp`/`face_scanned`/`sha256_original`, `metadata_exif`, `processing_inputs`, `ai_descriptions`, `people_faces`, `operation_history`, `checkpoints`, and `ai_search_fts` across photos, videos, documents, and named people; no separate per-stage `ai_manifest` table). Adding new folders skips archive-root runtime files, backfills `sha256_original`, migrates legacy `ai_manifest` / `stage4_insightface_checkpoint.json`, processes only new/unscanned media, preserves existing `ai_descriptions` and manual `people_faces` names/clusters, and keeps `server.py` synced with `builder.py`.
6. Strict two-folder archive contract & optional family sharing:
   * **`content/`**: `images/YYYY/`, `videos/YYYY/`, and `documents/`.
   * **`viewer/`**: Self-contained Offline Museum (`viewer.html` with Daylight/Darkroom themes + mobile `@media (max-width: 640px)` 2-column responsive layout & touch-swipe lightbox + Video Viewer tab + **🔒 Lock** `/__museum_logout` + live `/__museum_events`/`/__museum_ping` DOM-wipe guard on server stop, `catalog.sqlite` with FTS5 `ai_search_fts`, `catalog_data.js` including `ALL_VIDEOS`, `catalog.json`, `avatars/`, `.processing_cache/`, HTTP `206` byte-range `server.py` with `/api/search`, `/api/open-media`, direct `.sqlite` & `share_config.json` `403` download blocking, `share_config.json` / `MUSEUM_PASSWORD` / `--password` + `--read-only` strict `401` pre-login gate enforcing `Content-Security-Policy: default-src 'none'`, per-IP + global login `429` rate limiting, and authenticated `GET /` `302 -> /viewer/viewer.html` redirect for Tailscale, LAN, or Cloudflare Tunnel links, plus `start_viewer.command` / `start_viewer.bat`).

---

## 2. Reference Implementation

The original 5-stage pipeline schemas and scripts are preserved in `reference_pipeline/viewer/pipeline/` (alongside the live-API viewer template in `memory_setup/viewer/template/`):

Key reference files to consult:
* [`config.py`](reference_pipeline/viewer/pipeline/config.py): Master paths & DB configuration
* [`db.py`](reference_pipeline/viewer/pipeline/db.py): SQLite schema, tables, indices, and transactions
* [`geocoder.py`](reference_pipeline/viewer/pipeline/geocoder.py): Bundled offline KD-Tree reverse geocoder (15k world cities)
* [`stage1_consolidate.py`](reference_pipeline/viewer/pipeline/stage1_consolidate.py): Atomic ingestion, file grouping, companion linkage
* [`stage2_standardize.py`](reference_pipeline/viewer/pipeline/stage2_standardize.py): Fast EXIF & proxy generation
* [`stage3_validate.py`](reference_pipeline/viewer/pipeline/stage3_validate.py): Validation, dHash, and deterministic AI manifest
* [`stage4_insightface.py`](reference_pipeline/viewer/pipeline/stage4_insightface.py): ArcFace ResNet-50 biometric face extraction
* [`stage4_clustering.py`](reference_pipeline/viewer/pipeline/stage4_clustering.py): Vector-accelerated NumPy BLAS Leader Centroid clustering (thresh=0.48)
* [`stage4_vision.py`](reference_pipeline/viewer/pipeline/stage4_vision.py): Memory-bounded Ollama Moondream vision processor with periodic recycling
* [`stage5_museum.py`](reference_pipeline/viewer/pipeline/stage5_museum.py): FTS5 search indexing & museum export
* [`export_viewer_data.py`](reference_pipeline/viewer/pipeline/export_viewer_data.py): Generates `catalog_data.js` and `catalog.json`
* [`server.py`](memory_setup/viewer/template/server.py): Zero-dependency local HTTP 206 streaming & `/api/open-media` server
* [`viewer.html`](reference_pipeline/viewer/viewer.html): Master read-only Offline Museum UI

---

## 3. Strict Architectural Decisions

1. **Memory Ceiling (< 2.5 GB)**:
   * Upstream `llama-server` defaults to an 8 GiB host prompt cache (`--cache-ram 8192`).
   * Standalone app passes `cache_prompt: false`, sets `LLAMA_ARG_CACHE_RAM=0`, clamps `num_ctx: 1024` / `num_predict: 128`, and recycles the runner (`keep_alive: 0`) every `50` items (`whisper`) or `200` items (`turbo`), backed by an RSS watchdog (`3072 MB` limit).
2. **Single Worker for VLM**:
   * Never run concurrent VLM threads. 1 worker only.
3. **High-Precision Document Classification**:
   * Family faces disqualify (`has_faces == True` $\to$ False).
   * Event settings disqualify (birthday cakes, churches, beaches, weddings).
   * Require compound phrases (`page from a notebook`, `math equations`, `worksheet`).
4. **Anti-Clobber UI Architecture**:
   * `viewer.html` is immutable. Data is injected via external `catalog_data.js` and queried via `catalog.sqlite`.
5. **Local Web Streaming Launcher**:
   * Modern Chromium-based browsers block parent directory traversal (`../content/`) under `file://`.
   * The application must launch a local lightweight streaming server or run within an embedded webview (e.g. PyWebView).
