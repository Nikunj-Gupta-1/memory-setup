# Master Project Requirements & System Execution Ledger

**Project**: Local Media-Processing Pipeline & Autonomous Museum Engine  
**Archive**: 33,771 personal media files (2007–2026)  
**Root Path**: `<archive_root>` (local gitignored `Memory_Setup/archive/`)  
**Ledger Created**: November 12, 2026  
**Status**: Continuously Maintained System Ledger  

---

## 1. Host Hardware & System Environment

All pipeline stages and intelligence engines are deployed on local client hardware with zero external cloud dependencies and zero API costs:

* **Hardware Model**: Apple MacBook Pro (14-inch)
* **Processor (SoC)**: Apple M2 Pro
  * **CPU**: 10-core CPU (6 performance cores, 4 efficiency cores)
  * **GPU**: 16-core GPU
  * **Neural Engine**: 16-core Apple Neural Engine (ANE) capable of 15.8 trillion operations/sec
* **Memory**: 16 GB Unified Memory (LPDDR5, 200 GB/s memory bandwidth)
* **Storage**: 512 GB NVMe SSD formatted as **Apple File System (APFS)**
* **Host Operating System**: macOS 27.2 (Darwin 25.x / arm64)
* **Storage Safety Limit**: Strict hard threshold of $\ge 15.0\text{ GiB}$ free disk space enforced before any batch generation.

---

## 2. Complete Environment & Toolchain Evolution

Below is the chronological record of every tool, compiler, runtime, and environment configuration introduced since the inception of the project:

| Category | Component / Tool | Version | Purpose in Pipeline | Configuration / Execution Mode |
| :--- | :--- | :---: | :--- | :--- |
| **System Python** | CPython | 3.15.2 | Initial pipeline stages (1, 2, 3, 5) | System Homebrew (`/opt/homebrew/bin/python3`) |
| **Upgrade Python** | CPython (via `uv`) | 3.12.16 | Biometric Face Engine (Stage 4 Upgrade) | Consolidated repo venv (`Memory_Setup/.venv/bin/python`) for precompiled ARM64 ONNX wheels |
| **Package Manager** | `uv` | Latest | Ultra-fast binary package manager | Manages isolated Python 3.12 virtualenv and C-extensions |
| **Native Compiler** | Apple Swift (`swiftc`) | 6.0 (Xcode) | Hardware-accelerated Apple Vision scanner | Compiled with `-O -whole-module-optimization` to produce `bin_face_extractor` |
| **Image CLI** | macOS `sips` | Native | Apple HEIC $\rightarrow$ JPEG proxy conversion | Multi-threaded subprocess pool (`ThreadPoolExecutor(max_workers=8)`) |
| **Database** | SQLite3 | 3.46+ | Master catalog, spatial queries, and FTS5 search | WAL mode (`PRAGMA journal_mode=WAL`), memory caches, FTS5 virtual table |
| **Local VLM** | Ollama | 0.1.x+ | Local Vision AI descriptions & homework detection | Runs `moondream:latest` (1.6B params, Q4_K_M) bound to `localhost:11434` |
| **Web Presentation** | Leaflet.js | 1.9.4 | Interactive World Map visualization | Bundled with keyless Esri ArcGIS Online tiles (Streets, Satellite, Dark, Topo) |

---

## 3. Python Dependency Inventory

Below are the exact pinned libraries deployed across the project runtimes:

```
# Core Computer Vision & Image Processing
pillow==13.0.0               # Byte-level format verification, dHash perceptual hashing, thumbnail generation
numpy==2.5.4                 # Pre-allocated BLAS vector math, Cosine similarities, clustering centroids
opencv-python==5.0.0.93      # Affine warping, image normalization, canonical 112x112 face matrix transforms
opencv-python-headless==5.0.0.93
scikit-image==0.26.0         # Image metrics & landmark geometry
scipy==1.18.1                # KD-Tree spatial coordinates & distance matrices
imageio==2.38.1              # Animation & multi-frame parsing
tifffile==2026.11.8          # Deep-color TIFF parsing

# Deep Learning & Face Biometrics
insightface==2.1             # SCRFD-10GF 5-point face detector & ArcFace ResNet-50 feature extractor
onnx==1.23.2                 # Open Neural Network Exchange runtime parser
onnxruntime==1.31.1          # Hardware-accelerated inference with CoreMLExecutionProvider

# Utilities, Graph & Networking
tqdm==4.70.1                 # Command-line progress tracking
requests==2.34.2             # Local Ollama HTTP bridge
networkx==3.7                # Graph-based relationship clustering
flatbuffers==25.12.19        # Serialized model tensor format
protobuf==7.36.2             # Protocol buffer serialization
```

---

## 4. Computational Resource Usage & Timing Ledger per Stage

| Stage & Pipeline Component | Hardware Utilized | Peak RAM | Disk Footprint Delta | Elapsed Execution Time | Throughput / Metric |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Stage 1: Consolidation & Grouping** | CPU (Single-thread I/O + APFS rename) | ~140 MB | **0 bytes** (APFS pointer renames) | **6.8 minutes** | 33,771 files organized into `images/YYYY/` & `videos/YYYY/` (82.7 items/s) |
| **Stage 2: Technical EXIF & Geocoding** | CPU (Multi-core Python + SQLite WAL) | ~280 MB | +12 MB (`catalog.sqlite`) | **4.2 minutes** | 32,424 metadata records; 20,608 photos reverse-geocoded offline (128.6 items/s) |
| **Stage 2: Proxy Refresh (`sips`)** | 8 CPU Performance Cores (`sips`) | ~650 MB | +841 MB (`.processing_cache/`) | **15.7 minutes** | 14,679 `.heic` photos converted to 1600px color JPEGs (15.6 img/s) |
| **Stage 3: Validation & AI Manifest** | 8 CPU Performance Cores (Pillow + dHash) | ~520 MB | +35 MB (Manifest JSON/CSV) | **3.1 minutes** | 32,424 images validated; 98 undated photos matched via dHash (173.2 img/s) |
| **Stage 4: Apple Vision Face Scan** | **Apple Neural Engine (ANE)** via Swift | ~420 MB | +18 MB (`people_faces` table) | **5.2 minutes** | 32,424 images scanned; 43,119 faces detected & feature-printed (**103.6 img/s**) |
| **Stage 4: BLAS Leader Clustering** | Apple Accelerate BLAS (NumPy matrix dot) | ~380 MB | +2 MB (Avatars & ranks) | **1.37 seconds** | 43,119 768-d vectors clustered; Top 9 family members ranked (31,473 vectors/s) |
| **Stage 4: Vision Model Benchmark** | M2 Pro GPU via Ollama | ~4.2 GB | +1 MB (Calibration logs) | **2.5 minutes** | Benchmarked 64 photos: Moondream (1.82s/img) vs Gemma4 (41.65s/img) |
| **Stage 5: Search & Museum Export** | CPU (SQLite FTS5 + JSON serializer) | ~310 MB | +34 MB (`catalog.json` + `data.js`) | **12.4 seconds** | 32,424 records indexed in FTS5; generated `viewer.html` & `catalog_data.js` |
| **Directory Restructure (2-Folder)** | APFS Inode Re-parenting (`os.rename`) | ~40 MB | **0 bytes** | **0.08 seconds** | 33,771 master files cleanly isolated into `content/` and `viewer/` |
| **Stage 4 Upgrade: InsightFace Rescan** | Apple Silicon CoreML + CPU (`buffalo_l`) | ~810 MB | +86 MB (`catalog.sqlite`) | **31.2 minutes** | 32,424 master photos scanned; **67,390 faces detected** with 5-point alignment (**17.3 img/s**) |
| **Stage 4 Upgrade: ArcFace BLAS Cluster** | Apple Accelerate BLAS (512-d unit vectors) | ~490 MB | +2 MB (Avatars & ranks) | **8.92 seconds** | 67,390 vectors clustered (thresh=0.48); **Top 9 family coverage expanded from 4.9k to 24.3k faces** |
| **Stage 4: Vision Memory Watchdog & VLM** | M2 Pro GPU via Ollama (`moondream`) | ~2.2 GB (Bounded) | +12 MB (`catalog.sqlite`) | Continuous Background | Memory-bounded 10-image runner recycling active (`num_ctx=1024`) |
| **Stage 5: Zero-Config Streaming Server** | Python 3 `http.server` (port 8080) | ~25 MB | 0 bytes | Instant | Serves museum web application smoothly at `http://localhost:8080/viewer/viewer.html` |
| **Standalone App (`Memory_Setup`)** | Python 3.12 (`uv` `.venv`), `pywebview` + HTTP/SSE GUI | ~40 MB (Control Center) | <5 MB (Code + `cities15000.sqlite` + `.app`; `.venv` gitignored) | Published (`29/29` pytest pass) | Cross-platform macOS/Windows zero-Terminal GUI & CLI at `https://github.com/Nikunj-Gupta-1/memory-setup` |

---

## 5. Storage Space Ledger

* **Initial Available Disk Space**: 29.53 GiB
* **Current Available Disk Space**: **49.59 GiB** (November 14, 2026)
* **Disk Space Safety Status**: **PASSED** (well above the 15.0 GiB safety floor).
* **Storage Breakdown**:
  * **Master Media (`content/`)**: ~214 GiB (32,424 photos, 1,337 videos, 3 documents)
  * **Database (`viewer/catalog.sqlite`)**: 235 MB
  * **Proxy Cache (`viewer/.processing_cache/`)**: 841 MB (14,679 files)
  * **Avatar Crops (`viewer/avatars/`)**: 1.2 MB (30 files)
  * **Data Exports (`viewer/catalog.json` & `catalog_data.js`)**: 34.6 MB
  * **Python Virtualenv (`.venv/`, consolidated at repo root)**: ~285+ MB (with `[heif,biometrics,gui,dev]`)
