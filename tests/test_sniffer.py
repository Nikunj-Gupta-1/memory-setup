from __future__ import annotations

from pathlib import Path

from memory_setup.ingestion.sniffer import sha256_file, sniff_file, sniff_magic_bytes


def test_sniff_magic_bytes_common_formats() -> None:
    assert sniff_magic_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF")["format"] == "JPEG"
    assert sniff_magic_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")["format"] == "PNG"
    assert sniff_magic_bytes(b"%PDF-1.7\n...")["media_type"] == "document"
    assert sniff_magic_bytes(b"\x00\x00\x00\x18ftypheic\x00\x00")["format"] == "HEIC"
    assert sniff_magic_bytes(b"\x00\x00\x00\x20ftypqt  \x00\x00")["media_type"] == "video"
    assert sniff_magic_bytes(b"RIFF\x00\x00\x00\x00WEBP")["format"] == "WEBP"


def test_sniff_file_overrides_wrong_extension(tmp_path: Path) -> None:
    # A real PNG disguised with a .jpg extension
    disguised = tmp_path / "photo_fake_ext.jpg"
    disguised.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    info = sniff_file(disguised)
    assert info["format"] == "PNG"
    assert info["media_type"] == "photo"
    assert info["ext"] == ".png"


def test_sha256_file_deterministic(tmp_path: Path) -> None:
    f = tmp_path / "sample.bin"
    f.write_bytes(b"hello memory setup")
    h1 = sha256_file(f)
    h2 = sha256_file(f)
    assert h1 == h2
    assert len(h1) == 64
