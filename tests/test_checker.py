from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from memory_setup.setup.checker import check_disk_space, check_ollama, run_diagnostics
from memory_setup.setup.installer import BIOMETRICS_PACKAGES, build_biometrics_install_command


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


def test_build_biometrics_install_command_uses_uv_when_pip_missing() -> None:
    with (
        patch("memory_setup.setup.installer.importlib.util.find_spec", return_value=None),
        patch("memory_setup.setup.installer.shutil.which", return_value="/opt/homebrew/bin/uv"),
    ):
        cmd = build_biometrics_install_command("/tmp/.venv/bin/python")

    assert cmd == ["/opt/homebrew/bin/uv", "pip", "install", "--python", "/tmp/.venv/bin/python", *BIOMETRICS_PACKAGES]


def test_build_biometrics_install_command_uses_pip_when_present() -> None:
    with (
        patch("memory_setup.setup.installer.importlib.util.find_spec", return_value=object()),
        patch("memory_setup.setup.installer.shutil.which", return_value=None),
    ):
        cmd = build_biometrics_install_command("/tmp/.venv/bin/python")

    assert cmd == ["/tmp/.venv/bin/python", "-m", "pip", "install", *BIOMETRICS_PACKAGES]


def test_check_tailscale_status_returns_connected_ip_and_message() -> None:
    import json
    from memory_setup.setup.checker import check_tailscale_status

    fake_json = json.dumps(
        {
            "BackendState": "Running",
            "TailscaleIPs": ["100.116.41.118", "fd7a:115c:a1e0::1"],
            "Self": {
                "DNSName": "macbook.tail7c6def.ts.net.",
                "HostName": "MacBook",
            },
        }
    )
    fake_proc = MagicMock(stdout=fake_json, returncode=0)
    with (
        patch("memory_setup.setup.checker._find_tailscale_binary", return_value="/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
        patch("memory_setup.setup.checker.subprocess.run", return_value=fake_proc),
    ):
        info = check_tailscale_status()

    assert info["installed"] is True
    assert info["connected"] is True
    assert info["running"] is True
    assert info["ipv4"] == "100.116.41.118"
    assert info["tailscale_ip"] == "100.116.41.118"
    assert info["dns_name"] == "macbook.tail7c6def.ts.net"
    assert "100.116.41.118" in info["message"]


def test_check_tailscale_status_interface_ip_fallback() -> None:
    from memory_setup.setup.checker import check_tailscale_status

    fake_ifconfig = MagicMock(
        stdout="utun6: flags=8051<UP,POINTOPOINT,RUNNING>\n\tinet 100.101.22.33 --> 100.101.22.33 netmask 0xffffffff\n",
        returncode=0,
    )
    with (
        patch("memory_setup.setup.checker._find_tailscale_binary", return_value=None),
        patch("memory_setup.setup.checker.platform.system", return_value="Darwin"),
        patch("memory_setup.setup.checker.subprocess.run", return_value=fake_ifconfig),
    ):
        info = check_tailscale_status()

    assert info["installed"] is True
    assert info["connected"] is True
    assert info["tailscale_ip"] == "100.101.22.33"
    assert info["ipv4"] == "100.101.22.33"


def test_cloudflared_status_and_installer(tmp_path: Path) -> None:
    import io
    import tarfile
    from memory_setup.setup.checker import check_cloudflared_status, find_cloudflared_binary
    from memory_setup.setup.installer import install_cloudflared

    fake_bin = tmp_path / "cloudflared-mock"
    fake_bin.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    with patch("memory_setup.setup.checker.shutil.which", return_value=str(fake_bin)):
        fake_ver = MagicMock(stdout="cloudflared version 2025.2.0 (built 2025-02-01)", stderr="", returncode=0)
        with patch("memory_setup.setup.checker.subprocess.run", return_value=fake_ver):
            status = check_cloudflared_status()
            assert find_cloudflared_binary() == str(fake_bin)
    assert status["installed"] is True
    assert status["binary_path"] == str(fake_bin)
    assert "2025.2.0" in str(status["version"])

    # Mock downloading a Darwin .tgz release into an isolated dest_dir
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tf:
        payload = b"#!/bin/sh\necho cloudflared\n"
        ti = tarfile.TarInfo("cloudflared")
        ti.size = len(payload)
        tf.addfile(ti, io.BytesIO(payload))
    tar_bytes = tar_buf.getvalue()

    class _FakeResp:
        def __init__(self, data: bytes) -> None:
            self._data = data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def raise_for_status(self) -> None:
            return None
        def iter_content(self, chunk_size: int = 65536):
            yield self._data

    dest = tmp_path / "cf-bin"
    with (
        patch("memory_setup.setup.installer.find_cloudflared_binary", return_value=None),
        patch("memory_setup.setup.installer._cloudflared_release_url", return_value=("https://example.test/cf.tgz", True)),
        patch("memory_setup.setup.installer.requests.get", return_value=_FakeResp(tar_bytes)),
        patch("memory_setup.setup.installer.platform.system", return_value="Darwin"),
    ):
        res = install_cloudflared(dest_dir=dest)
    assert res["ok"] is True, res
    installed_bin = Path(res["binary_path"])
    assert installed_bin.exists()
    assert installed_bin.read_bytes().startswith(b"#!/bin/sh")
