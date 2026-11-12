# Memory Setup — Local Media Intelligence Engine & Offline Museum

**Memory Setup** is a privacy-first, 100% offline media intelligence engine and **Offline Museum** builder. It transforms any unsorted folder of photos, videos, HEIC captures, Apple Live Photos, screenshots, and camera exports into a searchable local archive with facial recognition, offline GPS reverse-geocoding, and memory-bounded local Vision AI captions — without ever sending a file to the cloud.

Built and battle-tested on a real **33,771-file personal archive (2007–2026)**, this repository contains both:
1. **`memory_setup/`** — The universal standalone desktop Control Center GUI, headless CLI, and Python package.
2. **`reference_pipeline/`** — The original 5-stage pipeline scripts, Swift Apple Vision extractor, museum HTML/server bundle, and architectural ledgers.

---

## Key Features & Engineering Safeguards

* **Strict Two-Folder Archive Contract**:
  * `content/` — User media only (`images/YYYY/`, `videos/YYYY/`, `documents/`), deduplicated by SHA-256 and paired with Live Photo `.mov` / `.aae` / `.xmp` companions under permanent sequential IDs (`P_000001`, `V_000001`, `S_000001`).
  * `viewer/` — Self-contained Offline Museum runtime (`catalog.sqlite` + SQLite FTS5 `ai_search_fts`, `catalog_data.js`, `catalog.json`, `avatars/`, `.processing_cache/`, `server.py`, `viewer.html`, `start_viewer.command`).
* **Two Ingestion Modes**:
  * **In-Place Reorganization** (`--inplace`): Atomic filesystem moves directly inside your target archive.
  * **Safe Copy** (`--safe-copy`): Copies from external SD cards / source folders into a clean target archive without modifying originals.
* **RAM-Bounded Local Vision AI (Apple Silicon UMA Safe)**:
  * Strictly single-worker (`concurrency = 1`) local Ollama Moondream client with `cache_prompt: false`, `num_ctx: 1024`, `num_predict: 128`, `temperature: 0`, `LLAMA_ARG_CACHE_RAM=0`, and automated `keep_alive: 0` runner recycling (**Whisper Mode** every `50` items / **Turbo Mode** every `200` items) plus an RSS watchdog.
* **ArcFace Biometrics & Vectorized BLAS Clustering**:
  * Optional InsightFace `buffalo_l` 512-d face embeddings (`[biometrics]` extra) paired with dynamic-capacity NumPy BLAS Leader Centroid Clustering (`thresh=0.48`) and `160x160` portrait avatar crops.
* **High-Precision Homework & Document Quarantine**:
  * Isolates math notebooks, worksheets, receipts, and code screenshots while disqualifying photos with detected faces or celebration scenes (birthday cakes, weddings, churches, dining tables).
* **Offline Spatial Reverse-Geocoder**:
  * `scipy.spatial.cKDTree` over 3D ECEF coordinates with a strict `150.0 km` Haversine cutoff so remote ocean coordinates never false-match distant coastal cities.
* **Anti-Clobber Museum Protection & 15.0 GiB Disk Safety Floor**:
  * Never overwrites a customized `viewer/viewer.html` or manual `person_name` edits during rebuilds (`--overwrite-html` required to reset HTML), and halts heavy ingestion if free disk drops below `15.0 GiB`.

---

## Repository Layout

```text
Memory_Setup/
├── memory_setup/                  # Standalone Python package & Control Center GUI
│   ├── cli.py                     # CLI entrypoint (`memory-setup`)
│   ├── config.py                  # ArchiveContext & two-folder path resolver
│   ├── db.py                      # Unified SQLite schema, migrations & read-only mode
│   ├── pipeline_runner.py         # Multi-stage orchestrator & archive metrics
│   ├── setup/                     # Hardware, disk, ExifTool/sips & Ollama diagnostics
│   ├── ingestion/                 # Magic-byte sniffer, companion pairing, Stages 1-3
│   ├── geocoding/                 # Offline 3D ECEF KD-Tree geocoder
│   ├── biometrics/                # InsightFace detector & BLAS Leader Clustering
│   ├── vision/                    # Document classifier & MemoryBoundedOllamaClient
│   ├── viewer/                    # Stage 5 FTS5 indexer, JS exporter & museum template
│   └── gui/                       # Local REST/SSE Control Center server & web UI
├── reference_pipeline/            # Original reference archive pipeline & architecture logs
│   ├── AGENT.md
│   └── viewer/
│       ├── pipeline/              # Original Stage 1-5 scripts + face_extractor.swift
│       ├── viewer.html            # Production Offline Museum single-page UI
│       ├── server.py              # Zero-dependency museum server + live rename/merge API
│       ├── start_viewer.command   # 1-click archive museum launcher
│       └── *.md                   # Manifesto, Progress Report, System Ledger & Handoff
├── tests/                         # 23 unit & integration tests (offline/mocked VLM)
├── pyproject.toml
├── Start_Memory_Setup.command     # 1-click macOS launcher
└── Start_Memory_Setup.bat         # 1-click Windows launcher
```

---

## Quick Start

### Option A: Windows Step-by-Step Setup Guide

Follow these steps on **Windows 10 / Windows 11**:

#### 1. Install Prerequisites
1. **Python 3.10 – 3.12** *(3.12 recommended)*:
   * Download from [python.org/downloads/windows](https://www.python.org/downloads/windows/).
   * **Important:** On the very first screen of the installer, check **"Add python.exe to PATH"** before clicking *Install Now*.
   * Verify in Command Prompt or PowerShell:
     ```bat
     python --version
     ```
2. **Ollama (for local AI photo descriptions)**:
   * Download and install **Ollama for Windows** from [ollama.com/download/windows](https://ollama.com/download/windows).
   * Open PowerShell or Command Prompt and pull the Moondream vision model (`~1.7 GB`):
     ```bat
     ollama pull moondream:latest
     ```
3. *(Optional)* **ExifTool** (faster metadata & video GPS extraction; falls back to built-in Pillow EXIF automatically if skipped):
   ```bat
   winget install OliverBetcher.ExifTool
   ```
4. *(Optional, for Face Recognition)* **Microsoft Visual C++ Redistributable**:
   * Required on some fresh Windows PCs by ONNX Runtime / InsightFace. Install the **x64 Visual C++ Redistributable** from Microsoft if adding `[biometrics]`.

#### 2. Download & Launch Memory Setup
* **Using Git:**
  ```bat
  git clone https://github.com/Nikunj-Gupta-1/memory-setup.git
  cd memory-setup
  ```
* **Or without Git:** Click **Code -> Download ZIP** on GitHub, right-click the downloaded `.zip` -> **Extract All...**, and open the extracted `memory-setup` folder.
* Double-click **`Start_Memory_Setup.bat`** (or run `.\Start_Memory_Setup.bat` from Command Prompt/PowerShell).
  * *If Windows Defender SmartScreen shows "Windows protected your PC"*: click **More info** -> **Run anyway**.
  * On first run, it automatically creates `.venv`, installs dependencies (`memory-setup` + `pillow-heif` for iPhone `.heic` photos), and opens the **Control Center** in your browser at `http://127.0.0.1:8765`.

#### 3. (Optional) Enable Face Detection & Clustering on Windows
Open Command Prompt / PowerShell inside the `memory-setup` folder and run once:
```bat
.venv\Scripts\pip.exe install -e ".[biometrics]"
```

#### 4. Safe First Test on Windows
1. Copy **20–50 test photos** into a sample folder (for example `C:\Users\<You>\Desktop\TestPhotos`).
2. In the browser Control Center (`http://127.0.0.1:8765`):
   * Click **Browse Folder** (or paste your archive path) and pick a destination folder like `C:\Users\<You>\Desktop\TestMuseum`.
   * Click **Run Pre-Flight Check** to confirm Python, free disk space, HEIC support, and Ollama are green.
   * Select **Safe Copy Mode** (so `TestPhotos` is never altered), pick **Whisper Mode**, and click **Start Pipeline**.
3. When Stage 5 finishes, click **Open Offline Museum** (or double-click `start_viewer.bat` inside `TestMuseum`).

#### Windows CLI Reference
```bat
REM Pre-flight diagnostics
.venv\Scripts\memory-setup.exe --setup

REM Safe Copy from source photos into a brand-new Offline Museum folder
.venv\Scripts\memory-setup.exe run C:\Path\To\NewArchive --safe-copy --source C:\Path\To\RawPhotos --whisper

REM Rebuild only Offline Museum search/index files
.venv\Scripts\memory-setup.exe sync C:\Path\To\NewArchive

REM View archive counts safely (read-only)
.venv\Scripts\memory-setup.exe status C:\Path\To\NewArchive
```

---

### Option B: macOS Quick Start

1. **Prerequisites**: Python 3.10+ (`python3 --version`), [Ollama](https://ollama.com) (`ollama pull moondream:latest`), and optional `brew install exiftool`.
2. **Clone & Launch**:
   ```bash
   git clone https://github.com/Nikunj-Gupta-1/memory-setup.git
   cd memory-setup
   chmod +x Start_Memory_Setup.command
   ./Start_Memory_Setup.command
   ```
   *(If macOS blocks an extracted ZIP script, run `xattr -cr .` once inside the folder.)*
3. **Optional Biometrics & Dev Tests**:
   ```bash
   .venv/bin/pip install -e ".[biometrics,dev]"
   ```
4. **CLI Usage**:
   ```bash
   .venv/bin/memory-setup --setup
   .venv/bin/memory-setup run /path/to/NewArchive --safe-copy --source /path/to/RawPhotos --whisper
   .venv/bin/memory-setup run /path/to/MyArchive --inplace --whisper
   .venv/bin/memory-setup sync /path/to/MyArchive
   .venv/bin/memory-setup status /path/to/MyArchive
   ```

---

## Running the Test Suite

All tests run cleanly offline using synthetic images, mocked Ollama HTTP responses, and temporary directories (`0` personal media required):

```bash
.venv/bin/pytest tests -v
```

---

## Archive Structure Created at Runtime

```text
YourArchive/
├── content/                  <-- User media only
│   ├── images/               (Year folders: 2024/20240615_120000__P_000001.jpg)
│   ├── videos/               (2024/20240615_120000__V_000001.mp4 & Live Photo companions)
│   └── documents/            (Quarantined homework, notes, screenshots)
└── viewer/                   <-- Offline Museum Runtime & AI Metadata
    ├── catalog.sqlite        (SQLite + FTS5 full-text search index)
    ├── catalog_data.js       (Zero-config offline JS bundle)
    ├── catalog.json          (Master JSON export)
    ├── avatars/              (160x160 person crops)
    ├── .processing_cache/    (Universal 1600px JPEG proxies)
    ├── reports/              (ai_manifest.json & ai_manifest.csv)
    ├── viewer.html           (Interactive Museum UI)
    ├── server.py             (Zero-dependency museum API & server)
    └── start_viewer.command  (1-click offline museum launcher)
```
