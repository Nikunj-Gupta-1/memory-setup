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
* **Offline Video Cinema & Mobile-Responsive Read-Only Museum UI (With Zero-Terminal Remote Password Gate)**:
  * Interactive read-only `viewer.html` (Warm Archival Daylight & Darkroom themes, plus dedicated mobile/phone `@media (max-width: 640px)` 2-column grids, zoom-safe `16px` search/select controls, and touch-swipe lightbox navigation) across both Photos and **Videos** (`ALL_VIDEOS`), HTTP `206` byte-range video streaming in `server.py` and the Control Center, Live Photo clip toggles, **People** & **World Map** filtering, **Open in System Player** (`/api/open-media`) + download fallbacks for legacy `.avi` clips, read-only `/api/search` FTS5 endpoint, a **🔒 Lock** (`/__museum_logout`) button that wipes loaded data immediately, and zero-terminal password + port setup inside the Control Center's **Remote Family Share** tab (`share_config.json` + strict `401` `Content-Security-Policy: default-src 'none'` pre-login gate + `5/60s` per-IP login brute-force `429` rate limit + `/` -> `/viewer/viewer.html` redirect + instant `/__museum_events` & `/__museum_ping` auto-logout across all connected devices the moment the server stops).
* **Incremental Re-Run Safe & Consolidated Single-Schema SQLite**:
  * Drop new photo/video folders into an existing archive and re-run **Organize & Build Museum** (or trigger individual stages in **Stage-by-Stage Runner**): all stages share one consolidated `viewer/catalog.sqlite` schema (no duplicate per-stage tables), runtime scripts/DB files at archive root are ignored, exact duplicates are caught cleanly, only new media enters Stages 1–4B, and existing AI captions, custom people names, and merged clusters are preserved unless **Force Reprocess Existing Media** (`--reprocess`) or **Force Full Face Re-Cluster** (`--recluster`) is checked in the Control Center Studio.
* **Anti-Clobber Museum Protection & 15.0 GiB Disk Safety Floor**:
  * Never overwrites a customized `viewer/viewer.html` or manual `person_name` edits during rebuilds (`--overwrite-html` required to reset HTML), keeps all mutating controls inside the **Memory Setup Studio Control Center**, and halts heavy ingestion if free disk drops below `15.0 GiB`.

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
│       ├── viewer.html            # Read-only Offline Museum single-page UI
│       ├── server.py              # Zero-dependency museum server (HTTP 206 streaming)
│       ├── start_viewer.command   # 1-click archive museum launcher
│       └── *.md                   # Manifesto, Progress Report, System Ledger & Handoff
├── scripts/                       # Native macOS universal launcher C source
├── tests/                         # 44 unit & integration tests (offline/mocked VLM)
├── pyproject.toml
├── Memory Setup.app/              # 1-click zero-Terminal macOS app bundle
├── Start_Memory_Setup.command     # macOS setup, --rebuild-app & detached launcher
└── Start_Memory_Setup.bat         # Windows setup, --rebuild-app & silent pythonw launcher
```

---

## Quick Start (100% GUI Control Center — No Terminal Required)

### Option A: Windows 10 / Windows 11 Step-by-Step Guide

Memory Setup launches directly as a native desktop **Control Center** window (backed by a local server starting on port `8765`, with automatic browser fallback if Edge WebView2 is absent).

#### 1. One-Time Prerequisites on Windows 10 / Windows 11
1. **Python 3.10 – 3.12** *(3.12 recommended)*:
   * Download from [python.org/downloads/windows](https://www.python.org/downloads/windows/).
   * **Important:** On the very first installer screen, check **"Add python.exe to PATH"** before clicking *Install Now*.
2. **Ollama (for local AI photo descriptions)**:
   * Download and install **Ollama for Windows** from [ollama.com/download/windows](https://ollama.com/download/windows).
   * After opening **Memory Setup**, click **Start Ollama** and **Pull Moondream** right inside the app (no commands needed!).
3. **Optional System Components**:
   * **Microsoft Edge WebView2 Runtime**: Pre-installed on Windows 11 and most Windows 10 systems for the native app window (automatically falls back to your default browser if missing).
   * **Microsoft Visual C++ Redistributable**: Only needed on fresh Windows PCs if you enable Face Recognition (`Install Face AI` button in the GUI).
   * **ExifTool** *(optional)*: Install if you want deep video/RAW camera tag parsing (falls back cleanly to Pillow EXIF).

#### 2. Download, First-Time Setup & Silent Shortcut
1. Click **Code -> Download ZIP** on GitHub -> right-click the `.zip` -> **Extract All...** (or run `git clone https://github.com/Nikunj-Gupta-1/memory-setup.git`).
2. Open the extracted folder and double-click **`Start_Memory_Setup.bat`**.
   * *If Windows Defender SmartScreen appears*: click **More info -> Run anyway**.
   * On first launch, it creates `.venv`, installs dependencies (`pillow-heif` for iPhone `.heic` photos + `pywebview` GUI), generates a silent **`Memory Setup.lnk`** shortcut with custom logo icon, and launches the Control Center via `pythonw.exe` without keeping a console window open.
3. **Every launch after first setup:** Double-click **`Memory Setup.lnk`** (opens with custom icon and zero console flash — you can pin it to your Desktop or Taskbar while keeping this folder in place) or re-run `Start_Memory_Setup.bat`. After pulling repo updates, run `Start_Memory_Setup.bat --rebuild-app` to refresh the `.venv` package and regenerate `Memory Setup.lnk`.

---

### Option B: macOS (Apple Silicon / Intel, macOS 12.0+) Step-by-Step Guide

#### 1. One-Time Prerequisites on macOS
1. **Python 3.10+** (`python3 --version`; Python `3.12` or `uv` recommended) and [Ollama for macOS](https://ollama.com).
2. *(Optional)* **`exiftool`** (`brew install exiftool`) for extended video/RAW metadata (`sips` + `pillow-heif` handle HEIC photos automatically).

#### 2. Download, First-Time Setup & Zero-Terminal Launch
1. Download via `git clone https://github.com/Nikunj-Gupta-1/memory-setup.git`, or **Code -> Download ZIP** and unzip in Finder.
2. **If you downloaded the GitHub `.zip`** (macOS adds quarantine flags and `.zip` archives strip executable bits), open Terminal in the extracted folder once and run:
   ```bash
   xattr -cr .
   chmod +x Start_Memory_Setup.command "Memory Setup.app/Contents/MacOS/MemorySetup"
   ```
3. **First launch:** Double-click **`Memory Setup.app`** (or **`Start_Memory_Setup.command`**).
   * If `.venv` has not been created yet, `Memory Setup.app` automatically opens `Start_Memory_Setup.command` once in Terminal to build `.venv` (using `uv` Python `3.12` if installed, else `python3 -m venv`), installs `.[heif,gui]`, detaches the Control Center, and closes its temporary Terminal window.
4. **Every launch after first setup:** Double-click **`Memory Setup.app`** directly for a clean native window (`"Memory Setup"` in the Dock/menu bar with custom icon and **zero Terminal window**; background logs are written to `~/Library/Logs/MemorySetup.log`).
   * Closing the window with the macOS red close icon or `Cmd+Q` signals any running stage to pause safely and exits cleanly without hanging.
   * After pulling repo updates or recreating the bundle from scratch, run `./Start_Memory_Setup.command --rebuild-app` (deletes and rebuilds `Memory Setup.app`, generates its universal binary + `.icns`, and refreshes editable `.venv`).

---

### Inside the Control Center Studio (Windows & macOS)

The Control Center opens with a persistent Active Archive bar, live execution progress strip, and left sidebar menu organizing workflows into focused panels (with clear descriptions right beside every control):
1. **Overview & Run**: Displays 8 live SQLite telemetry cards (`Total Media`, `Master Photos`, `Videos`, `GPS Mapped`, `Faces Detected`, `People Clusters`, `AI Captioned`, `Docs Flagged`) alongside Master Controls (**Organize & Build Museum**, **Resume Vision AI Only**, **Pause & Flush RAM**, and **Open Offline Museum Viewer**).
2. **Stage-by-Stage Runner**: Trigger individual pipeline stages cleanly on demand — **Stage 1: Discovery & Deduplication**, **Stage 2: EXIF, Offline GPS & HEIC**, **Stage 3: Chronological Folders & dHash**, **Stages 1–3 Combined (Ingest Only)**, **Stage 4A: Face Detection & Clustering**, **Stage 4A-Fast: Re-Cluster Saved Faces Only** (`<2s`), **Stage 4B: Vision AI & Homework Scan**, and **Stage 5: Sync Museum & Search**.
3. **Curation & People**: **Manage & Merge People** (rename or merge clusters atomically across SQLite + FTS5 + `catalog_data.js`), **Quarantine Docs** (`content/documents/homework_archive/`), and **Refresh Museum Data**.
4. **Storage & AI Profile**: Configure **In-Place Move** vs. **Safe Copy** (with Source folder picker), **Whisper** (`50`-item recycle) vs. **Turbo** (`200`-item recycle), master AI toggles, and **Incremental & Rebuild Safety Controls** (`Force Reprocess Existing Media`, `Force Full Face Re-Cluster`).
5. **System Readiness**: Inspect disk safety floor (`>= 15.0 GiB`), launch Ollama (**Start Ollama**), download vision weights (**Pull Moondream**), install biometrics (**Install Face AI**), and verify ExifTool / HEIC decoders.
6. **Remote Family Share**: Configure the museum password (`share_config.json`) & share port, toggle the read-only remote Museum server (`0.0.0.0:<port>`) with zero terminal commands, detect/launch **Tailscale** or 1-click install & run a **Cloudflare Quick Tunnel** (`*.trycloudflare.com` HTTPS URL), copy ready-to-share **Tailscale Private IP** / **MagicDNS** / **Cloudflare HTTPS** / **Home Wi-Fi** links, and follow the built-in Tailscale & Cloudflare sharing guide.
7. **Show All Panels**: Unified single-scroll layout displaying every panel simultaneously.

### Private Remote Family Access (Zero Terminal Required, Museum Viewer Only)

You can share **only the read-only Offline Museum Viewer** (`viewer.html` + `/api/search` + HTTP 206 video streaming) directly from the Control Center without opening Terminal — while the Control Center itself remains strictly bound to `127.0.0.1`:
1. Open the **Remote Family Share** tab in the Control Center sidebar.
2. Enter a family password under **Password Lock & Port Setup** and click **Save Password & Port** (saved locally in `archive/viewer/share_config.json`, ignored by Git and blocked from HTTP downloads).
   * Unauthenticated requests receive only an inline login gate (`401` + `Content-Security-Policy: default-src 'none'`), protected against brute-force guessing by an automatic `429` rate limit after `5` failed attempts per `60s`. Not a single byte of `viewer.html`, `catalog_data.js`, thumbnails, photos, or videos loads until the password is entered. Visitors can click **🔒 Lock** in the top-right bar of `viewer.html` to end their session anytime.
3. Choose **Option A (Private Tailscale VPN)** or **Option B (1-Click Public Cloudflare HTTPS Link)**:
   * **Option A — Tailscale VPN:** Click **Open Tailscale App** and connect Tailscale on this machine (`tailscale.com/download`) — the Control Center polls Tailscale status automatically (including interface `100.x.y.z` fallback) and fills in your **Tailscale Private IP Link** — then click **Start Remote Museum Server**.
   * **Option B — 1-Click Cloudflare Quick Tunnel:** Click **Start Public Cloudflare Link** (or click **Install Cloudflare Tunnel** once if `cloudflared` is not installed yet — it downloads cleanly into `~/.memory_setup/bin`). The Control Center starts the password-locked server automatically and generates a temporary `https://*.trycloudflare.com/viewer/viewer.html` link.
4. Copy the generated link and share the password separately with family. Clicking **🛑 Stop Remote Server** (or closing the Control Center) terminates any active Cloudflare tunnel, stops the remote server, and broadcasts an SSE/heartbeat logout signal that locks every open browser/phone and wipes loaded media from the screen right away. (Headless servers can alternatively launch `server.py --host 127.0.0.1 --port 8080 --read-only --no-browser` with `MUSEUM_PASSWORD=...`.)

<details>
<summary>Optional Headless CLI Commands (Advanced Users)</summary>

Pass arguments directly to **`Start_Memory_Setup.command`** (macOS) or **`Start_Memory_Setup.bat`** (Windows), or activate `.venv` first (`source .venv/bin/activate` on macOS, `.venv\Scripts\activate` on Windows) and run `memory-setup`:

```bash
# Pre-flight hardware, disk, tool, and Ollama diagnostics
./Start_Memory_Setup.command --setup      # Windows: .\Start_Memory_Setup.bat --setup

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

All tests run cleanly offline using synthetic images, mocked Ollama HTTP responses, and temporary directories (`0` personal media required). First ensure the `dev` extra (`pytest`) is installed, then run `pytest`:

```bash
# macOS / Linux
.venv/bin/python -m pip install -e ".[dev]"   # (or: uv pip install --python .venv/bin/python -e ".[dev]")
.venv/bin/pytest tests -v
```

```bat
:: Windows
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\pytest.exe tests -v
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
    ├── catalog.sqlite        (Unified SQLite DB + FTS5 search index across all stages)
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
