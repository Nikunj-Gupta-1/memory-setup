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
│   ├── geocoding/                 # Offline 3D ECEF KD-Tree geocoder + cities15000.sqlite
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
├── scripts/                       # Native macOS universal launcher C source
├── tests/                         # 29 unit & integration tests (offline/mocked VLM)
├── pyproject.toml
├── Memory Setup.app/              # 1-click zero-Terminal macOS app bundle
├── Start_Memory_Setup.command     # macOS setup & detached launcher
└── Start_Memory_Setup.bat         # Windows setup & silent pythonw/shortcut launcher
```

---

## Quick Start (100% GUI Control Center — No Terminal Required)

### Option A: Windows Step-by-Step Guide

Everything works directly inside the graphical **Control Center** (`http://127.0.0.1:8765`) after double-clicking `Start_Memory_Setup.bat`:

#### 1. One-Time Prerequisites on Windows 10 / Windows 11
1. **Python 3.10 – 3.12** *(3.12 recommended)*:
   * Download from [python.org/downloads/windows](https://www.python.org/downloads/windows/).
   * **Important:** On the very first installer screen, check **"Add python.exe to PATH"** before clicking *Install Now*.
2. **Ollama (for local AI photo descriptions)**:
   * Download and install **Ollama for Windows** from [ollama.com/download/windows](https://ollama.com/download/windows).
   * After launching **Memory Setup**, you can click **Start Ollama** and **Pull Moondream** directly inside the GUI (no commands needed!).
3. *(Optional)* **Microsoft Visual C++ Redistributable**:
   * Only needed on some fresh Windows PCs if you enable Face Recognition (`Install Face AI` button in GUI).

#### 2. Download & Launch (Zero Terminal Commands)
1. Click **Code -> Download ZIP** on GitHub -> right-click the `.zip` -> **Extract All...** (or `git clone https://github.com/Nikunj-Gupta-1/memory-setup.git`).
2. Open the extracted folder and double-click **`Start_Memory_Setup.bat`**.
   * *If Windows Defender SmartScreen appears*: click **More info -> Run anyway**.
   * On first launch, it creates `.venv`, installs dependencies (`pillow-heif` for iPhone `.heic` photos + `pywebview` GUI), generates a silent **`Memory Setup.lnk`** shortcut with custom icon, and launches the Control Center via `pythonw.exe` without keeping a console window open.

#### 3. Complete Workflow Inside the GUI
1. **Setup Wizard Cards**:
   * Click **Start Ollama** and **Pull Moondream** (`~1.7 GB` download) to enable offline AI photo descriptions.
   * Click **Install Face AI** *(optional)* to install InsightFace ArcFace `buffalo_l` facial clustering right from the browser.
2. **Choose Folders & Mode**:
   * For **Safe First Test** (recommended with 20–50 sample photos): click **Safe Copy (Keep Originals)** -> pick an empty destination via **Browse Archive...** (e.g. `Desktop\TestMuseum`) and pick your sample folder via **Browse Source...** (`Desktop\TestPhotos`). Your original files are never touched.
   * Click **Audit Folder** to check free disk space (`>= 15.0 GiB` safety floor) and verify dependencies.
3. **Run & Browse**:
   * Choose **Whisper** (lowest RAM / coolest) or **Turbo**, then click **Organize & Build Museum**.
   * Use **Pause & Flush RAM** anytime to stop safely and resume later with **Resume Vision AI Only**.
   * Click **Quarantine Docs** to move flagged homework/notes into `content/documents/homework_archive/`, or click **Open Offline Museum Viewer** to explore timeline, world map, people clusters (rename/merge live), and full-text search.

---

### Option B: macOS Quick Start

1. **Prerequisites**: Python 3.10+ (`python3 --version`) and [Ollama](https://ollama.com).
2. **Launch Control Center**:
   * Double-click **`Memory Setup.app`** (or `Start_Memory_Setup.command` on first setup; run `xattr -cr .` first if downloaded as a ZIP).
   * After `.venv` is ready, `Memory Setup.app` launches directly with **no Terminal window**, custom app icon, and `"Memory Setup"` in the Dock/menu bar (`Start_Memory_Setup.command` also detaches and closes its Terminal window).
   * Use **Start Ollama**, **Pull Moondream**, **Install Face AI**, **Browse Archive / Browse Source**, and **Open Offline Museum Viewer** directly in the UI.

<details>
<summary>Optional Headless CLI Commands (Advanced Users)</summary>

```bash
# Pre-flight hardware, disk, tool, and Ollama diagnostics
memory-setup --setup

# Run full pipeline in Safe Copy or In-Place mode
memory-setup run /path/to/NewArchive --safe-copy --source /path/to/RawPhotos --whisper
memory-setup run /path/to/MyArchive --inplace --whisper

# Rebuild FTS5 index & Offline Museum assets / inspect status
memory-setup sync /path/to/MyArchive
memory-setup status /path/to/MyArchive
```

</details>

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
│   └── documents/            (Documents & quarantined homework under documents/homework_archive/)
├── start_viewer.command      (Top-level 1-click macOS museum launcher)
├── start_viewer.bat          (Top-level 1-click Windows museum launcher)
└── viewer/                   <-- Offline Museum Runtime & AI Metadata
    ├── content               (Relative symlink -> ../content)
    ├── catalog.sqlite        (SQLite + FTS5 full-text search index)
    ├── catalog_data.js       (Zero-config offline JS bundle)
    ├── catalog.json          (Master JSON export)
    ├── ai_manifest.json      (Validated processing manifest JSON)
    ├── ai_manifest.csv       (Validated processing manifest CSV)
    ├── avatars/              (160x160 person crops)
    ├── .processing_cache/    (Universal 1600px JPEG proxies)
    ├── reports/              (stage1..stage5 JSON verification reports)
    ├── viewer.html           (Interactive Museum UI)
    ├── server.py             (Zero-dependency museum API & server)
    ├── start_viewer.command  (1-click macOS museum launcher)
    └── start_viewer.bat      (1-click Windows museum launcher)
```
