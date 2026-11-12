from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from memory_setup.config import MIN_FREE_DISK_GB, OLLAMA_DEFAULT_MODEL, OLLAMA_DEFAULT_URL


def check_disk_space(path: Path) -> float:
    """Return free disk space in GiB for the filesystem containing `path`."""
    target = Path(path).expanduser().resolve()
    while not target.exists() and target.parent != target:
        target = target.parent
    usage = shutil.disk_usage(target)
    return usage.free / (1024 ** 3)


def estimate_directory_bytes(path: Path, max_files: int = 200_000) -> int:
    """Calculate total bytes inside `path` while skipping internal viewer/content dirs."""
    total = 0
    seen = 0
    if not path.exists():
        return 0
    skip_dirs = {"content", "viewer", ".processing_cache", "avatars", ".venv", ".git", ".trash"}
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d.lower() not in skip_dirs]
        for fname in files:
            if fname.startswith("."):
                continue
            fpath = Path(root) / fname
            try:
                total += fpath.stat().st_size
            except OSError:
                pass
            seen += 1
            if seen >= max_files:
                return total
    return total


def detect_onnx_providers() -> List[str]:
    """Detect available ONNX Runtime execution providers without loading heavy models."""
    try:
        import onnxruntime as ort  # type: ignore

        avail = ort.get_available_providers()
        preferred = [
            p
            for p in (
                "CoreMLExecutionProvider",
                "CUDAExecutionProvider",
                "DmlExecutionProvider",
                "CPUExecutionProvider",
            )
            if p in avail
        ]
        return preferred or avail
    except Exception:
        return []


def check_ollama(url: str = OLLAMA_DEFAULT_URL, model: str = OLLAMA_DEFAULT_MODEL) -> Dict[str, Any]:
    """Check whether Ollama daemon is reachable and target VLM model is installed."""
    base = url.rstrip("/")
    result: Dict[str, Any] = {
        "binary_installed": shutil.which("ollama") is not None or Path("/Applications/Ollama.app").exists(),
        "running": False,
        "models": [],
        "target_model": model,
        "target_model_ready": False,
        "error": None,
    }
    try:
        resp = requests.get(f"{base}/api/tags", timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            models = [m.get("name", "") for m in data.get("models", [])]
            result["running"] = True
            result["models"] = models
            target_base = model.split(":")[0]
            result["target_model_ready"] = any(
                m == model or m.startswith(f"{target_base}:") for m in models
            )
    except Exception as exc:
        result["error"] = str(exc)
    return result


def check_insightface_models() -> Dict[str, Any]:
    """Check if InsightFace Python module and buffalo_l weights are present."""
    has_pkg = False
    try:
        import insightface  # noqa: F401
        has_pkg = True
    except Exception:
        pass

    model_dir = Path.home() / ".insightface" / "models" / "buffalo_l"
    has_weights = model_dir.exists() and any(model_dir.glob("*.onnx"))
    return {
        "package_installed": has_pkg,
        "weights_cached": has_weights,
        "weights_dir": str(model_dir),
    }


def run_diagnostics(
    archive_path: Optional[Path] = None,
    mode: str = "in_place",
    ollama_url: str = OLLAMA_DEFAULT_URL,
    ollama_model: str = OLLAMA_DEFAULT_MODEL,
) -> Dict[str, Any]:
    """Run comprehensive system pre-flight diagnostics."""
    providers = detect_onnx_providers()
    ollama_info = check_ollama(ollama_url, ollama_model)
    insightface_info = check_insightface_models()

    heif_ready = shutil.which("sips") is not None
    pillow_heif_installed = False
    try:
        import pillow_heif  # noqa: F401
        pillow_heif_installed = True
        heif_ready = True
    except Exception:
        pass

    tools = {
        "python": {
            "version": platform.python_version(),
            "ok": sys.version_info >= (3, 10),
            "executable": sys.executable,
        },
        "exiftool": {
            "installed": shutil.which("exiftool") is not None,
            "fallback": "Pillow EXIF",
        },
        "sips": {
            "installed": shutil.which("sips") is not None,
            "pillow_heif": pillow_heif_installed,
            "heic_ready": heif_ready,
        },
        "magick": {"installed": shutil.which("magick") is not None},
        "swiftc": {"installed": shutil.which("swiftc") is not None},
        "pywebview": {"installed": _can_import("webview")},
    }

    disk_info: Dict[str, Any] = {
        "min_free_gb": MIN_FREE_DISK_GB,
        "free_gb": None,
        "source_gb": None,
        "required_gb": MIN_FREE_DISK_GB,
        "ok": True,
        "message": "Select an archive folder to audit disk space.",
    }
    if archive_path is not None:
        p = Path(archive_path).expanduser().resolve()
        free_gb = check_disk_space(p)
        src_gb = estimate_directory_bytes(p) / (1024 ** 3) if p.exists() else 0.0
        req_gb = MIN_FREE_DISK_GB if mode == "in_place" else (src_gb + MIN_FREE_DISK_GB)
        ok = p.exists() and free_gb >= req_gb
        disk_info = {
            "path": str(p),
            "exists": p.exists(),
            "mode": mode,
            "min_free_gb": MIN_FREE_DISK_GB,
            "free_gb": round(free_gb, 2),
            "source_gb": round(src_gb, 2),
            "required_gb": round(req_gb, 2),
            "ok": ok,
            "message": (
                f"{free_gb:.2f} GiB free (needs {req_gb:.2f} GiB)"
                if p.exists()
                else "Selected path does not exist."
            ),
        }

    return {
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "release": platform.release(),
        },
        "onnx_providers": providers,
        "biometrics": insightface_info,
        "ollama": ollama_info,
        "tools": tools,
        "disk": disk_info,
    }


def _can_import(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except Exception:
        return False
