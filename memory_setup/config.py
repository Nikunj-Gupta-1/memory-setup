from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Optional


PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".heic", ".heif", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif",
}
VIDEO_EXTENSIONS = {".mov", ".mp4", ".avi", ".m4v", ".3gp", ".mkv"}
SIDECAR_EXTENSIONS = {".aae", ".xmp"}
AUDIO_EXTENSIONS = {".wav", ".m4a", ".mp3"}
DOCUMENT_EXTENSIONS = {".doc", ".docx", ".pdf", ".txt"}

IGNORE_FILENAMES = {
    ".ds_store",
    "thumbs.db",
    "desktop.ini",
    "indexervolumeguid",
    "wpsettings.dat",
}

# Files generated at archive_root by Memory Setup / Offline Museum that must never be ingested
ARCHIVE_ROOT_IGNORE_FILENAMES = {
    "start_viewer.command",
    "start_viewer.bat",
    "agent.md",
    "project_manifesto.md",
    "cleanup_homework.sh",
    "commit_merges.py",
}

ARCHIVE_ROOT_IGNORE_PREFIXES = (
    "cleanup_homework",
    "commit_merges",
)

ARCHIVE_ROOT_IGNORE_EXTS = {
    ".lnk",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
    ".db",
}

IGNORE_DIRNAMES = {
    "$recycle.bin",
    "system volume information",
    ".trash",
    ".git",
    "_dedupe",
    "content",
    "viewer",
    ".processing_cache",
    "avatars",
    ".venv",
    "__pycache__",
    "__macosx",
    ".pytest_cache",
}

MIN_FREE_DISK_GB = 15.0

# Memory Watchdog & Ollama Defaults
OLLAMA_DEFAULT_URL = "http://127.0.0.1:11434"
OLLAMA_DEFAULT_MODEL = "moondream:latest"
OLLAMA_NUM_CTX = 1024
OLLAMA_NUM_PREDICT = 128
OLLAMA_RECYCLE_INTERVAL_TURBO = 200
OLLAMA_RECYCLE_INTERVAL_WHISPER = 50
OLLAMA_RSS_LIMIT_MB = 3072


@dataclass
class ArchiveContext:
    """Canonical path & configuration resolver enforcing the strict two-folder contract."""

    archive_root: Path
    source_dirs: List[Path] = field(default_factory=list)
    mode: Literal["in_place", "safe_copy"] = "in_place"
    speed_mode: Literal["whisper", "turbo"] = "whisper"
    min_free_disk_gb: float = MIN_FREE_DISK_GB
    ollama_url: str = OLLAMA_DEFAULT_URL
    ollama_model: str = OLLAMA_DEFAULT_MODEL
    enable_biometrics: bool = True
    enable_vision: bool = True
    reprocess: bool = False
    recluster: bool = False

    def __post_init__(self) -> None:
        self.archive_root = Path(self.archive_root).expanduser().resolve()
        if not self.source_dirs:
            self.source_dirs = [self.archive_root]
        else:
            self.source_dirs = [Path(p).expanduser().resolve() for p in self.source_dirs]

    @property
    def content_dir(self) -> Path:
        return self.archive_root / "content"

    @property
    def viewer_dir(self) -> Path:
        return self.archive_root / "viewer"

    @property
    def target_images_dir(self) -> Path:
        return self.content_dir / "images"

    @property
    def target_videos_dir(self) -> Path:
        return self.content_dir / "videos"

    @property
    def documents_dir(self) -> Path:
        return self.content_dir / "documents"

    @property
    def processing_cache_dir(self) -> Path:
        return self.viewer_dir / ".processing_cache"

    @property
    def avatars_dir(self) -> Path:
        return self.viewer_dir / "avatars"

    @property
    def reports_dir(self) -> Path:
        return self.viewer_dir / "reports"

    @property
    def db_path(self) -> Path:
        return self.viewer_dir / "catalog.sqlite"

    @property
    def json_catalog_path(self) -> Path:
        return self.viewer_dir / "catalog.json"

    @property
    def manifest_json_path(self) -> Path:
        return self.viewer_dir / "ai_manifest.json"

    @property
    def manifest_csv_path(self) -> Path:
        return self.viewer_dir / "ai_manifest.csv"

    @property
    def catalog_data_js_path(self) -> Path:
        return self.viewer_dir / "catalog_data.js"

    @property
    def viewer_html_path(self) -> Path:
        return self.viewer_dir / "viewer.html"

    @property
    def recycle_interval(self) -> int:
        return (
            OLLAMA_RECYCLE_INTERVAL_WHISPER
            if self.speed_mode == "whisper"
            else OLLAMA_RECYCLE_INTERVAL_TURBO
        )

    def resolve(self, rel_path: str) -> Path:
        """Resolve a stored relative path cleanly, supporting legacy .processing_cache prefixes."""
        norm = rel_path.replace("\\", "/").lstrip("/")
        candidate = self.archive_root / norm
        if candidate.exists():
            return candidate
        if norm.startswith(".processing_cache/"):
            return self.viewer_dir / norm
        if norm.startswith("viewer/"):
            alt = self.archive_root / norm
            if alt.exists():
                return alt
        return candidate

    def to_rel(self, full_path: Path) -> str:
        """Return POSIX path relative to archive_root."""
        return Path(full_path).resolve().relative_to(self.archive_root).as_posix()

    def to_viewer_web_path(self, rel_path: str) -> str:
        """Map an archive-relative path to a URL path relative to viewer/viewer.html."""
        norm = rel_path.replace("\\", "/").lstrip("/")
        if norm.startswith("viewer/"):
            return norm[len("viewer/"):]
        if norm.startswith(".processing_cache/"):
            return norm
        if norm.startswith("content/"):
            return "../" + norm
        return norm

    def ensure_layout(self) -> None:
        """Create the strict two-folder directory hierarchy."""
        self.target_images_dir.mkdir(parents=True, exist_ok=True)
        self.target_videos_dir.mkdir(parents=True, exist_ok=True)
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.viewer_dir.mkdir(parents=True, exist_ok=True)
        self.processing_cache_dir.mkdir(parents=True, exist_ok=True)
        self.avatars_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
