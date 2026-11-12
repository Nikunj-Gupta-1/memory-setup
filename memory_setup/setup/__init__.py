"""Setup diagnostics and guided dependency installer for Memory Setup."""

from memory_setup.setup.checker import run_diagnostics, check_disk_space, estimate_directory_bytes
from memory_setup.setup.installer import ensure_ollama_running, pull_ollama_model, install_guidance

__all__ = [
    "run_diagnostics",
    "check_disk_space",
    "estimate_directory_bytes",
    "ensure_ollama_running",
    "pull_ollama_model",
    "install_guidance",
]
