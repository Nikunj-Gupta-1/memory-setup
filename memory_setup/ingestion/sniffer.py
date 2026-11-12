from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Optional

from memory_setup.config import (
    AUDIO_EXTENSIONS,
    DOCUMENT_EXTENSIONS,
    PHOTO_EXTENSIONS,
    SIDECAR_EXTENSIONS,
    VIDEO_EXTENSIONS,
)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute SHA-256 digest via streaming chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as fp:
        while True:
            chunk = fp.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sniff_magic_bytes(header: bytes, ext_lower: str = "") -> Optional[Dict[str, str]]:
    """Inspect initial 32 bytes to determine true container format & category."""
    if len(header) >= 3 and header[:3] == b"\xff\xd8\xff":
        return {"format": "JPEG", "media_type": "photo", "ext": ".jpg"}
    if len(header) >= 8 and header[:8] == b"\x89PNG\r\n\x1a\n":
        return {"format": "PNG", "media_type": "photo", "ext": ".png"}
    if len(header) >= 6 and header[:6] in (b"GIF87a", b"GIF89a"):
        return {"format": "GIF", "media_type": "photo", "ext": ".gif"}
    if len(header) >= 2 and header[:2] == b"BM":
        return {"format": "BMP", "media_type": "photo", "ext": ".bmp"}
    if len(header) >= 4 and header[:4] in (b"II*\x00", b"MM\x00*"):
        return {"format": "TIFF", "media_type": "photo", "ext": ".tiff"}
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return {"format": "WEBP", "media_type": "photo", "ext": ".webp"}
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"AVI ":
        return {"format": "AVI", "media_type": "video", "ext": ".avi"}
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return {"format": "WAV", "media_type": "audio_memo", "ext": ".wav"}
    if len(header) >= 4 and header[:4] == b"\x1a\x45\xdf\xa3":
        return {"format": "MKV", "media_type": "video", "ext": ".mkv"}
    if len(header) >= 5 and header[:5] == b"%PDF-":
        return {"format": "PDF", "media_type": "document", "ext": ".pdf"}
    if len(header) >= 8 and header[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return {"format": "DOC", "media_type": "document", "ext": ".doc"}
    if len(header) >= 4 and header[:4] == b"PK\x03\x04" and ext_lower in (".docx", ".xlsx", ".pptx"):
        return {"format": "OOXML", "media_type": "document", "ext": ext_lower}

    # ISO Base Media File Format (HEIC, HEIF, MOV, MP4, M4V, M4A)
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"):
            return {"format": "HEIC", "media_type": "photo", "ext": ".heic"}
        if brand in (b"M4A ", b"m4a "):
            return {"format": "M4A", "media_type": "audio_memo", "ext": ".m4a"}
        if brand in (b"qt  ", b"isom", b"iso2", b"mp41", b"mp42", b"M4V ", b"3gp4", b"3gp5"):
            return {"format": "MP4/MOV", "media_type": "video", "ext": ext_lower or ".mp4"}

    return None


def sniff_file(path: Path) -> Dict[str, str]:
    """Identify media type and format using magic-bytes header with extension fallback."""
    ext_lower = path.suffix.lower()
    header = b""
    try:
        with open(path, "rb") as fp:
            header = fp.read(32)
    except OSError:
        pass

    magic = sniff_magic_bytes(header, ext_lower=ext_lower)
    if magic is not None:
        return magic

    if ext_lower in PHOTO_EXTENSIONS:
        return {"format": ext_lower.lstrip(".").upper(), "media_type": "photo", "ext": ext_lower}
    if ext_lower in VIDEO_EXTENSIONS:
        return {"format": ext_lower.lstrip(".").upper(), "media_type": "video", "ext": ext_lower}
    if ext_lower in SIDECAR_EXTENSIONS:
        return {"format": ext_lower.lstrip(".").upper(), "media_type": "sidecar", "ext": ext_lower}
    if ext_lower in AUDIO_EXTENSIONS:
        return {"format": ext_lower.lstrip(".").upper(), "media_type": "audio_memo", "ext": ext_lower}
    if ext_lower in DOCUMENT_EXTENSIONS:
        return {"format": ext_lower.lstrip(".").upper(), "media_type": "document", "ext": ext_lower}
    return {"format": "OTHER", "media_type": "other", "ext": ext_lower}
