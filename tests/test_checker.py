from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from memory_setup.setup.checker import check_disk_space, check_ollama, run_diagnostics


def test_check_disk_space_returns_positive_gib(tmp_path: Path) -> None:
    free_gb = check_disk_space(tmp_path)
    assert isinstance(free_gb, float)
    assert free_gb > 0.0


def test_check_ollama_mocked_reachable() -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"models": [{"name": "moondream:latest"}, {"name": "llama3.2:3b"}]}

    with patch("memory_setup.setup.checker.requests.get", return_value=mock_resp):
        info = check_ollama("http://127.0.0.1:11434", "moondream:latest")

    assert info["running"] is True
    assert info["target_model_ready"] is True
    assert "moondream:latest" in info["models"]


def test_check_ollama_mocked_unreachable() -> None:
    with patch("memory_setup.setup.checker.requests.get", side_effect=ConnectionError("refused")):
        info = check_ollama("http://127.0.0.1:11434", "moondream:latest")

    assert info["running"] is False
    assert info["target_model_ready"] is False
    assert info["error"] is not None


def test_run_diagnostics_low_disk_flag(tmp_path: Path) -> None:
    with (
        patch("memory_setup.setup.checker.check_disk_space", return_value=5.0),
        patch("memory_setup.setup.checker.check_ollama", return_value={"running": False, "target_model_ready": False, "models": [], "binary_installed": False, "target_model": "moondream:latest", "error": "down"}),
    ):
        diag = run_diagnostics(archive_path=tmp_path, mode="in_place")

    assert diag["disk"]["ok"] is False
    assert diag["disk"]["free_gb"] == 5.0
