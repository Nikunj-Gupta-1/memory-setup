import os
from pathlib import Path

# Workspace Root & Layout (resolved dynamically from viewer/pipeline/config.py)
VIEWER_DIR = Path(__file__).resolve().parent.parent  # <archive_root>/viewer
WORKSPACE_ROOT = VIEWER_DIR.parent                   # <archive_root>
CONTENT_DIR = WORKSPACE_ROOT / 'content'

# Destination Directories
CONTENT_DIR = WORKSPACE_ROOT / 'content'
SOURCE_DIRS = []
TARGET_IMAGES_DIR = CONTENT_DIR / 'images'
TARGET_VIDEOS_DIR = CONTENT_DIR / 'videos'
DOCUMENTS_DIR = CONTENT_DIR / 'documents'
EXCLUDED_DIR = CONTENT_DIR / 'documents'

# Cache and Output Paths
PROCESSING_CACHE_DIR = VIEWER_DIR / '.processing_cache'
AVATARS_DIR = VIEWER_DIR / 'avatars'
REPORTS_DIR = VIEWER_DIR / 'reports'
DB_PATH = VIEWER_DIR / 'catalog.sqlite'
JSON_CATALOG_PATH = VIEWER_DIR / 'catalog.json'
MANIFEST_JSON_PATH = VIEWER_DIR / 'ai_manifest.json'
MANIFEST_CSV_PATH = VIEWER_DIR / 'ai_manifest.csv'

# Extensions
PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.heic', '.png', '.webp', '.bmp', '.gif', '.tiff', '.tif'}
VIDEO_EXTENSIONS = {'.mov', '.mp4', '.avi', '.m4v', '.3gp', '.mkv'}
SIDECAR_EXTENSIONS = {'.aae', '.xmp'}
AUDIO_EXTENSIONS = {'.wav', '.m4a', '.mp3'}
DOCUMENT_EXTENSIONS = {'.doc', '.docx', '.pdf', '.txt'}

# Ignore patterns
IGNORE_FILENAMES = {'.ds_store', 'thumbs.db', 'desktop.ini', 'indexervolumeguid', 'wpsettings.dat'}
IGNORE_DIRNAMES = {'$recycle.bin', 'system volume information', '.trash', '.git', '_dedupe', 'content', 'viewer', '.processing_cache', 'avatars', '.venv'}

# Disk Safety Limit
MIN_FREE_DISK_GB = 15.0
