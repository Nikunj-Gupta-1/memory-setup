# AGENT.MD: System Knowledge & Operational Briefing

> **CRITICAL DIRECTIVE FOR ANY AGENT READING THIS FILE**:  
> Read this file completely before taking any actions. It contains the exact state, constraints, rules, and architectures of this repository.

---

## 1. Workspaces & Roles

This environment operates across two distinct workspaces:

1. **Active Media Archive & Museum Runtime**:  
   Path: `/Users/nikunjgupta/Downloads/images`  
   Contains 33,771 personal photos and videos spanning 2007–2026. Organized into a strict two-folder contract (`content/` and `viewer/`). Stage 4 Vision (`stage4_vision.py`) has captioned **8,809 / 32,424** photos (**21** flagged docs) as of November 14, 2026.
2. **Universal Standalone Application Repository**:  
   Path: `/Users/nikunjgupta/Downloads/PROJECTS/Memory_Setup`  
   **Implemented & Published**: Full GUI Control Center, CLI (`memory-setup`), 1-click Mac/Windows launchers, and `23/23` passing tests. Public GitHub repo: `https://github.com/Nikunj-Gupta-1/memory-setup`.

---

## 2. Directory Layout & Key Anchors

```
/Users/nikunjgupta/Downloads/images/
├── AGENT.md                            # This briefing document
├── start_viewer.command                # 1-click museum launcher symlink (run from Finder)
├── content/                            # PURE USER CONTENT (Do not place code here)
│   ├── images/YYYY/                    # 32,424 master photos partitioned by year (2007–2026)
│   ├── videos/YYYY/                    # 1,337 master videos partitioned by year (2008–2026)
│   └── documents/                      # Preserved non-media documents
└── viewer/                             # APPLICATION RUNTIME & PIPELINE
    ├── viewer.html                     # Offline Museum Web UI (timeline, search, map, people, cleanup)
    ├── server.py                       # Lightweight local streaming server (http://localhost:8080)
    ├── start_viewer.command            # Shell script launcher
    ├── catalog.sqlite                  # SQLite 3.46+ database (WAL enabled, FTS5 virtual search)
    ├── catalog_data.js                 # Browser payload for viewer.html (7.6 MB)
    ├── catalog.json                    # Full JSON export (33.4 MB)
    ├── ai_manifest.json                # Fixed AI manifest (26.8 MB, 32,424 items)
    ├── .processing_cache/              # 14,679 proxy JPEGs (841 MB) -> DO NOT PURGE
    ├── avatars/                        # Cropped family face avatars (cluster_0.jpg ... cluster_8.jpg)
    ├── content -> ../content           # Symlink for HTTP server routing
    ├── MASTER_PROGRESS_REPORT.md       # Full historical stage documentation
    ├── PROJECT_REQUIREMENTS_AND_SYSTEM_LEDGER.md # Resource ledger, timings, and pinned dependencies
    └── pipeline/                       # Modular Python & Swift pipeline
        ├── config.py                   # Master paths & DB configuration
        ├── db.py                       # SQLite schema helpers
        ├── geocoder.py                 # Offline KD-Tree reverse geocoder (15k world cities)
        ├── stage1_consolidate.py       # In-place file consolidation & companion pairing
        ├── stage2_standardize.py       # EXIF/GPS extraction & proxy generation
        ├── stage3_validate.py          # Perceptual hash validation & AI manifest generator
        ├── stage4_faces.py             # Apple Vision face detection via Swift ANE
        ├── stage4_clustering.py        # Vector-accelerated NumPy BLAS Leader Centroid clustering
        ├── stage4_insightface.py       # ArcFace ResNet-50 biometric pipeline (Python 3.12 .venv)
        ├── stage4_vision.py            # Local Ollama Moondream vision captioning (Memory-bounded)
        ├── stage5_museum.py            # FTS5 search & viewer generator (Anti-clobber protected)
        ├── export_viewer_data.py       # Exporter for catalog_data.js & catalog.json
        └── verify_stage.py             # Verification test suite for stages 1–5
```

---

## 3. Strict Operating Rules & Constraints

1. **BypassSandbox Rule**:  
   Always pass `BypassSandbox: true` on terminal commands involving Python scripts, Ollama CLI/API, macOS `sips`, Swift compiler, or SQLite writes.
2. **Preserve `.processing_cache/`**:  
   Do **NOT** delete or wipe `viewer/.processing_cache/` (841 MB, 14,679 files). It contains pre-rendered color JPEGs essential for Chromium viewer rendering and Ollama vision inference.
3. **Single-Worker VLM Constraint**:  
   Never launch multi-threaded or concurrent calls to Ollama. Inference must run strictly **1 image at a time**.
4. **Ollama Memory Watchdog & Safeguards**:
   * Upstream `llama-server` defaults to an 8 GiB host prompt cache (`--cache-ram 8192`).
   * When processing image streams, always clamp context: `"options": {"num_ctx": 1024, "num_predict": 128, "temperature": 0}`.
   * Periodically recycle the runner via `{"model": "moondream:latest", "keep_alive": 0}` (`stage4_vision.py` recycles every batch of 10 images; `Memory_Setup` recycles every 50 in Whisper / 200 in Turbo). In ~0.015s, `llama-server` unloads, flushing accumulated cache and Metal buffers back to macOS.
5. **Anti-Clobber Rule for `viewer.html`**:  
   Never overwrite `viewer/viewer.html` with dummy or sample templates. `viewer.html` is the full production UI with 35-city Leaflet mapping, family face managers, and timeline scrubbers. Data updates must only touch `catalog_data.js` and `catalog.json` via `export_viewer_data.py`.
6. **Homework / Document Classifier Rules**:  
   Do not use naive keyword matching (`'written on it'`, `'piece of paper'`). In `stage4_vision.py`, the high-precision classifier enforces:
   * Disqualify if faces are detected (`has_faces == True`).
   * Disqualify if narrative starts with person/couple/baby/group.
   * Disqualify on personal event settings (birthday cake, church, wedding, park, beach).
   * Require compound phrases (`page from a notebook`, `math equations`, `worksheet`, `textbook page`).

---

## 4. Useful Commands

* **Launch Museum Viewer**:
  ```bash
  cd /Users/nikunjgupta/Downloads/images/viewer && python3 server.py
  # Open in browser: http://localhost:8080/viewer/viewer.html
  ```
* **Run / Resume Background Vision Analysis**:
  ```bash
  cd /Users/nikunjgupta/Downloads/images/viewer && python3 pipeline/stage4_vision.py
  ```
* **Verify Pipeline Stages**:
  ```bash
  cd /Users/nikunjgupta/Downloads/images/viewer && python3 pipeline/verify_stage.py --stage 1
  # (Stages 1 through 5 supported)
  ```
* **Re-export Viewer Data after DB Updates**:
  ```bash
  cd /Users/nikunjgupta/Downloads/images/viewer && python3 pipeline/export_viewer_data.py
  ```
