from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from memory_setup.config import OLLAMA_DEFAULT_MODEL, OLLAMA_DEFAULT_URL
from memory_setup.setup.checker import check_ollama

BIOMETRICS_PACKAGES = (
    "insightface>=0.7.3",
    "onnx>=1.15.0",
    "onnxruntime>=1.17.0",
    "opencv-python-headless>=4.9.0",
)


def build_biometrics_install_command(python_executable: Optional[str] = None) -> List[str]:
    """Build package-install command supporting both stdlib pip venvs and seedless uv venvs."""
    py = python_executable or sys.executable
    has_pip = importlib.util.find_spec("pip") is not None
    uv_bin = shutil.which("uv")
    if not has_pip and uv_bin:
        return [uv_bin, "pip", "install", "--python", py, *BIOMETRICS_PACKAGES]
    if has_pip:
        return [py, "-m", "pip", "install", *BIOMETRICS_PACKAGES]
    if uv_bin:
        return [uv_bin, "pip", "install", "--python", py, *BIOMETRICS_PACKAGES]
    raise RuntimeError("Neither pip nor uv is available to install Face AI packages.")


def install_guidance() -> Dict[str, Any]:
    """Return platform-specific installation links and commands for missing dependencies."""
    sys_name = platform.system()
    if sys_name == "Darwin":
        ollama_url = "https://ollama.com/download/Ollama-darwin.zip"
        exiftool_cmd = "brew install exiftool"
    elif sys_name == "Windows":
        ollama_url = "https://ollama.com/download/OllamaSetup.exe"
        exiftool_cmd = "winget install OliverBetcher.ExifTool"
    else:
        ollama_url = "https://ollama.com/download/linux"
        exiftool_cmd = "sudo apt-get install libimage-exiftool-perl"

    return {
        "platform": sys_name,
        "ollama_download_url": ollama_url,
        "ollama_model_pull": f"ollama pull {OLLAMA_DEFAULT_MODEL}",
        "exiftool_install_cmd": exiftool_cmd,
    }


def ensure_ollama_running(
    url: str = OLLAMA_DEFAULT_URL,
    wait_seconds: float = 10.0,
) -> Dict[str, Any]:
    """Start `ollama serve` with LLAMA_ARG_CACHE_RAM=0 if Ollama is installed but stopped."""
    status = check_ollama(url)
    if status["running"]:
        return {"ok": True, "already_running": True, "status": status}

    ollama_bin = shutil.which("ollama")
    if not ollama_bin:
        return {
            "ok": False,
            "error": "Ollama binary not found on PATH.",
            "guidance": install_guidance(),
        }

    env = os.environ.copy()
    env["LLAMA_ARG_CACHE_RAM"] = "0"
    env.setdefault("OLLAMA_MAX_LOADED_MODELS", "1")

    subprocess.Popen(
        [ollama_bin, "serve"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        status = check_ollama(url)
        if status["running"]:
            return {"ok": True, "already_running": False, "status": status}
        time.sleep(0.5)

    return {"ok": False, "error": "Timed out waiting for Ollama daemon to start."}


def pull_ollama_model(
    model: str = OLLAMA_DEFAULT_MODEL,
    url: str = OLLAMA_DEFAULT_URL,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Pull an Ollama model via streaming HTTP API with progress callbacks."""
    base = url.rstrip("/")
    try:
        with requests.post(
            f"{base}/api/pull",
            json={"name": model, "stream": True},
            stream=True,
            timeout=600,
        ) as resp:
            resp.raise_for_status()
            last_status: Dict[str, Any] = {"status": "starting"}
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                    last_status = evt
                    if progress_cb:
                        progress_cb(evt)
                except json.JSONDecodeError:
                    continue
        return {"ok": True, "model": model, "last_event": last_status}
    except Exception as exc:
        return {"ok": False, "model": model, "error": str(exc)}
