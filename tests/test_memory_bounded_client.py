from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from memory_setup.vision.ollama_client import MemoryBoundedOllamaClient


def test_ollama_payload_enforces_memory_bounds() -> None:
    client = MemoryBoundedOllamaClient(
        model="moondream:latest",
        num_ctx=1024,
        num_predict=128,
        recycle_interval=3,
    )
    payload = client.build_generate_payload("AAAA")
    assert payload["cache_prompt"] is False
    assert payload["stream"] is False
    assert payload["options"]["num_ctx"] == 1024
    assert payload["options"]["num_predict"] == 128
    assert payload["options"]["temperature"] == 0


def test_ollama_client_recycles_every_n_requests(tmp_path: Path) -> None:
    img = tmp_path / "proxy.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0test")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"response": "A sunny beach."}
    mock_resp.raise_for_status.return_value = None

    client = MemoryBoundedOllamaClient(recycle_interval=2, rss_limit_mb=99999)

    with (
        patch("memory_setup.vision.ollama_client.requests.post", return_value=mock_resp) as mock_post,
        patch("memory_setup.vision.ollama_client.get_llama_server_rss_mb", return_value=500.0),
        patch("memory_setup.vision.ollama_client.time.sleep", return_value=None),
    ):
        assert client.describe_image(img) == "A sunny beach."
        # 1st call -> only generate call
        assert mock_post.call_count == 1

        assert client.describe_image(img) == "A sunny beach."
        # 2nd call hits recycle_interval=2 -> generate + keep_alive:0 recycle
        assert mock_post.call_count == 3
        recycle_payload = mock_post.call_args_list[-1].kwargs["json"]
        assert recycle_payload["keep_alive"] == 0
