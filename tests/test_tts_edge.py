"""Free TTS fallback behavior."""

from pathlib import Path

import pytest

from c2video.config.schema import TTSConfig
from c2video.tts import edge_impl


@pytest.mark.asyncio
async def test_macos_fallback_runs_after_edge_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingCommunicate:
        def __init__(self, **_kwargs) -> None:
            pass

        async def save(self, _path: str) -> None:
            raise OSError("offline")

    async def no_sleep(_seconds: float) -> None:
        return None

    def fake_macos(_text: str, output: Path) -> Path:
        output.write_bytes(b"test-audio")
        return output

    monkeypatch.setattr(edge_impl.edge_tts, "Communicate", FailingCommunicate)
    monkeypatch.setattr(edge_impl.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(edge_impl.sys, "platform", "darwin")
    monkeypatch.setattr(edge_impl, "_macos_synthesize", fake_macos)

    output = tmp_path / "speech.mp3"
    provider = edge_impl.EdgeTTSProvider(TTSConfig())
    result = await provider.synthesize("测试", output)

    assert result == output
    assert output.read_bytes() == b"test-audio"
