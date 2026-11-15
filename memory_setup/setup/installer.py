from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from memory_setup.config import OLLAMA_DEFAULT_MODEL, OLLAMA_DEFAULT_URL
from memory_setup.setup.checker import check_ollama, find_cloudflared_binary

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


def _cloudflared_release_url(system_name: Optional[str] = None, machine_name: Optional[str] = None) -> tuple[str, bool]:
    """Return official Cloudflare GitHub release URL and whether it is a `.tgz` archive."""
    sys_name = system_name or platform.system()
    arch_raw = (machine_name or platform.machine()).lower()
    is_arm = arch_raw in {"arm64", "aarch64"}

    if sys_name == "Darwin":
        asset = "cloudflared-darwin-arm64.tgz" if is_arm else "cloudflared-darwin-amd64.tgz"
        return f"https://github.com/cloudflare/cloudflared/releases/latest/download/{asset}", True
    if sys_name == "Windows":
        asset = "cloudflared-windows-amd64.exe" if ("64" in arch_raw or is_arm) else "cloudflared-windows-386.exe"
        return f"https://github.com/cloudflare/cloudflared/releases/latest/download/{asset}", False
    asset = "cloudflared-linux-arm64" if is_arm else "cloudflared-linux-amd64"
    return f"https://github.com/cloudflare/cloudflared/releases/latest/download/{asset}", False


def install_cloudflared(dest_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Download/install the standalone `cloudflared` binary with zero Terminal usage."""
    existing = find_cloudflared_binary()
    if existing:
        return {"ok": True, "already_installed": True, "binary_path": existing}

    bin_dir = (dest_dir or (Path.home() / ".memory_setup" / "bin")).expanduser().resolve()
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe_name = "cloudflared.exe" if platform.system() == "Windows" else "cloudflared"
    target_bin = bin_dir / exe_name
    url, is_tgz = _cloudflared_release_url()

    try:
        download_path = bin_dir / ("cloudflared.tgz.tmp" if is_tgz else f"{exe_name}.tmp")
        with requests.get(url, stream=True, timeout=120, allow_redirects=True) as resp:
            resp.raise_for_status()
            with open(download_path, "wb") as fp:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        fp.write(chunk)

        if is_tgz:
            with tarfile.open(download_path, "r:gz") as tf:
                member = next((m for m in tf.getmembers() if m.isfile() and Path(m.name).name == "cloudflared"), None)
                if member is None:
                    raise RuntimeError("Downloaded archive did not contain cloudflared binary.")
                extracted = tf.extractfile(member)
                if extracted is None:
                    raise RuntimeError("Could not read cloudflared from archive.")
                tmp_bin = bin_dir / f"{exe_name}.tmp"
                with open(tmp_bin, "wb") as out_fp:
                    shutil.copyfileobj(extracted, out_fp)
                download_path.unlink(missing_ok=True)
                tmp_bin.replace(target_bin)
        else:
            download_path.replace(target_bin)

        if platform.system() != "Windows":
            os.chmod(target_bin, 0o755)
        return {"ok": True, "already_installed": False, "binary_path": str(target_bin)}
    except Exception as dl_exc:
        # Optional OS package manager fallback (Homebrew on macOS, winget on Windows)
        if platform.system() == "Darwin":
            brew_bin = shutil.which("brew") or (
                "/opt/homebrew/bin/brew"
                if Path("/opt/homebrew/bin/brew").is_file()
                else ("/usr/local/bin/brew" if Path("/usr/local/bin/brew").is_file() else None)
            )
            if brew_bin:
                proc = subprocess.run([brew_bin, "install", "cloudflared"], capture_output=True, text=True, timeout=180)
                found = find_cloudflared_binary()
                if proc.returncode == 0 and found:
                    return {"ok": True, "already_installed": False, "binary_path": found}
        elif platform.system() == "Windows":
            winget_bin = shutil.which("winget")
            if winget_bin:
                proc = subprocess.run(
                    [
                        winget_bin,
                        "install",
                        "--id",
                        "Cloudflare.cloudflared",
                        "-e",
                        "--accept-source-agreements",
                        "--accept-package-agreements",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                found = find_cloudflared_binary()
                if proc.returncode == 0 and found:
                    return {"ok": True, "already_installed": False, "binary_path": found}
        return {"ok": False, "error": f"Could not install cloudflared: {dl_exc}"}
