from __future__ import annotations

import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import subprocess
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


def get_lan_ip() -> Optional[str]:
    """Return the local LAN IPv4 address for home Wi-Fi sharing without sending traffic."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    finally:
        sock.close()
    return None


def _find_tailscale_binary() -> Optional[str]:
    candidates = [
        shutil.which("tailscale"),
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        "/opt/homebrew/bin/tailscale",
        "/usr/local/bin/tailscale",
        r"C:\Program Files\Tailscale\tailscale.exe",
    ]
    for cand in candidates:
        if cand and Path(cand).exists():
            return str(cand)
    return None


def _detect_tailscale_interface_ip() -> Optional[str]:
    """Inspect active network interfaces for an IPv4 address in Tailscale's CGNAT range (100.64.0.0/10)."""
    cmd = ["ipconfig"] if platform.system() == "Windows" else ["ifconfig"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
    except Exception:
        return None
    cgnat = ipaddress.ip_network("100.64.0.0/10")
    for match in re.findall(r"\b(100\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", proc.stdout or ""):
        try:
            addr = ipaddress.ip_address(match)
        except ValueError:
            continue
        if addr in cgnat:
            return str(addr)
    return None


def _pick_ipv4(candidates: Any) -> Optional[str]:
    if not isinstance(candidates, list):
        return None
    for item in candidates:
        s = str(item or "").strip()
        if "." in s and ":" not in s:
            return s
    return None


def check_tailscale_status() -> Dict[str, Any]:
    """Inspect local Tailscale installation, tunnel state, Tailscale IP, and MagicDNS name."""
    bin_path = _find_tailscale_binary()
    info: Dict[str, Any] = {
        "installed": bin_path is not None,
        "binary_path": bin_path,
        "backend_state": "NotInstalled" if bin_path is None else "Unknown",
        "running": False,
        "connected": False,
        "ipv4": None,
        "tailscale_ip": None,
        "dns_name": None,
        "hostname": None,
        "message": "Tailscale not installed (optional for remote access)",
        "error": None,
    }
    if not bin_path:
        iface_ip = _detect_tailscale_interface_ip()
        if iface_ip:
            info.update(
                {
                    "installed": True,
                    "backend_state": "Running",
                    "running": True,
                    "connected": True,
                    "ipv4": iface_ip,
                    "tailscale_ip": iface_ip,
                    "message": f"Tailscale VPN connected ({iface_ip})",
                }
            )
        return info

    try:
        proc = subprocess.run(
            [bin_path, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        raw = (proc.stdout or "").strip()
        if not raw:
            info["backend_state"] = "Stopped"
            info["message"] = "Tailscale installed (Disconnected — open Tailscale to connect)"
            return info
        data = json.loads(raw)
        state = str(data.get("BackendState") or "Stopped")
        is_connected = state.lower() == "running"
        self_info = data.get("Self") or {}
        ipv4 = (
            _pick_ipv4(data.get("TailscaleIPs"))
            or _pick_ipv4(self_info.get("TailscaleIPs"))
            or (_detect_tailscale_interface_ip() if is_connected else None)
        )
        info["backend_state"] = state
        info["running"] = is_connected
        info["connected"] = is_connected
        info["ipv4"] = ipv4
        info["tailscale_ip"] = ipv4
        if self_info.get("DNSName"):
            info["dns_name"] = str(self_info["DNSName"]).rstrip(".")
        if self_info.get("HostName"):
            info["hostname"] = str(self_info["HostName"])
        if is_connected:
            info["message"] = f"Tailscale VPN connected ({ipv4})" if ipv4 else "Tailscale VPN connected"
        elif state.lower() == "needslogin":
            info["message"] = "Tailscale needs sign-in (open Tailscale app to log in)"
        else:
            info["message"] = "Tailscale installed (Disconnected — open Tailscale to connect)"
    except Exception as exc:
        info["error"] = str(exc)
        iface_ip = _detect_tailscale_interface_ip()
        if iface_ip:
            info.update(
                {
                    "backend_state": "Running",
                    "running": True,
                    "connected": True,
                    "ipv4": iface_ip,
                    "tailscale_ip": iface_ip,
                    "message": f"Tailscale VPN connected ({iface_ip})",
                }
            )
        else:
            info["message"] = "Tailscale installed (unable to query status)"
    return info
