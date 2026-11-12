# AGENT.MD: Universal Standalone Application Engineering Briefing

> **CRITICAL DIRECTIVE FOR ANY AGENT WORKING ON MEMORY_SETUP**:  
> Read this file, along with `standalone_app_implementation_plan.md` in the brain artifacts directory, before writing code or modifying this repository.

---

## 1. Project Goal

Transform the proven 5-stage media intelligence pipeline developed in `/Users/nikunjgupta/Downloads/images` into an **autonomous, zero-configuration standalone desktop application with a GUI** located here in `/Users/nikunjgupta/Downloads/PROJECTS/Memory_Setup`.

The user contract:
1. Ingest any chaotic folder of photos/videos in any arbitrary nested hierarchy.
2. Provide a 1-click Setup Wizard for missing dependencies (Ollama, models, ONNX runtimes).
3. Offer an **In-Place Reorganization Mode** (atomic APFS/NTFS renames, 0 extra disk space) and a **Safe Copy Mode** (preserves originals).
4. Run the pipeline with strict memory bounds (single-worker, `<2.5 GB` RAM ceiling via runner recycling).
5. Output strictly two folders in the user's destination:
   * **`content/`**: `images/YYYY/`, `videos/YYYY/`, and `documents/`.
   * **`viewer/`**: Complete self-contained museum web application (`viewer.html`, `catalog.sqlite` with FTS5 search, `catalog_data.js`, face avatars, proxy cache, and zero-config local streaming launcher).

---

## 2. Reference Implementation

All production logic, schemas, and algorithms are battle-tested and operational in:
`/Users/nikunjgupta/Downloads/images/viewer/pipeline/`

Key reference files to consult:
* [`config.py`](file:///Users/nikunjgupta/Downloads/images/viewer/pipeline/config.py): Master paths & DB configuration
* [`db.py`](file:///Users/nikunjgupta/Downloads/images/viewer/pipeline/db.py): SQLite schema, tables, indices, and transactions
* [`geocoder.py`](file:///Users/nikunjgupta/Downloads/images/viewer/pipeline/geocoder.py): Bundled offline KD-Tree reverse geocoder (15k world cities)
* [`stage1_consolidate.py`](file:///Users/nikunjgupta/Downloads/images/viewer/pipeline/stage1_consolidate.py): Atomic ingestion, file grouping, companion linkage
* [`stage2_standardize.py`](file:///Users/nikunjgupta/Downloads/images/viewer/pipeline/stage2_standardize.py): Fast EXIF & proxy generation
* [`stage3_validate.py`](file:///Users/nikunjgupta/Downloads/images/viewer/pipeline/stage3_validate.py): Validation, dHash, and deterministic AI manifest
* [`stage4_insightface.py`](file:///Users/nikunjgupta/Downloads/images/viewer/pipeline/stage4_insightface.py): ArcFace ResNet-50 biometric face extraction
* [`stage4_clustering.py`](file:///Users/nikunjgupta/Downloads/images/viewer/pipeline/stage4_clustering.py): Vector-accelerated NumPy BLAS Leader Centroid clustering (thresh=0.48)
* [`stage4_vision.py`](file:///Users/nikunjgupta/Downloads/images/viewer/pipeline/stage4_vision.py): Memory-bounded Ollama Moondream vision processor with periodic recycling
* [`stage5_museum.py`](file:///Users/nikunjgupta/Downloads/images/viewer/pipeline/stage5_museum.py): FTS5 search indexing & museum export
* [`export_viewer_data.py`](file:///Users/nikunjgupta/Downloads/images/viewer/pipeline/export_viewer_data.py): Generates `catalog_data.js` and `catalog.json`
* [`server.py`](file:///Users/nikunjgupta/Downloads/images/viewer/server.py): Zero-dependency local streaming HTTP server
* [`viewer.html`](file:///Users/nikunjgupta/Downloads/images/viewer/viewer.html): Master museum UI

---

## 3. Strict Architectural Decisions

1. **Memory Ceiling (< 2.5 GB)**:
   * Upstream `llama-server` defaults to an 8 GiB host prompt cache (`--cache-ram 8192`).
   * Standalone app must pass `cache_prompt: false` (or set `LLAMA_ARG_CACHE_RAM=0`), clamp context to `num_ctx: 1024`, and recycle the model runner (`keep_alive: 0`) every 200 images.
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
