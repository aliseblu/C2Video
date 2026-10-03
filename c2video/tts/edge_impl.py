"""Edge TTS provider — free, no API key required.

Uses the `edge-tts` library. This provider is the default.
If the Edge endpoint is unavailable, fall back to the macOS or Windows
inbox speech engine so local end-to-end runs still complete.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import edge_tts

from c2video.config.schema import TTSConfig
from c2video.media_tools import ffmpeg_path
from c2video.tts.base import TTSProvider


def _encode_audio(source: Path, output_path: Path) -> Path:
    output_path.unlink(missing_ok=True)
    completed = subprocess.run(
        [
            ffmpeg_path(),
            "-y",
            "-i",
            str(source),
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )
    source.unlink(missing_ok=True)
    if (
        completed.returncode != 0
        or not output_path.exists()
        or output_path.stat().st_size < 1000
    ):
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg could not encode local speech: {completed.stderr[-500:]}")
    return output_path


def _macos_synthesize(text: str, output_path: Path) -> Path:
    """macOS built-in Mandarin voice → AIFF → MP3."""
    import tempfile
    import uuid

    aiff_path = Path(tempfile.gettempdir()) / f"c2video-{uuid.uuid4().hex}.aiff"
    try:
        completed = subprocess.run(
            ["say", "-v", "Tingting", "-o", str(aiff_path), text],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        aiff_path.unlink(missing_ok=True)
        raise RuntimeError("macOS speech synthesis timed out") from exc
    if (
        completed.returncode != 0
        or not aiff_path.exists()
        or aiff_path.stat().st_size <= 4096
    ):
        aiff_path.unlink(missing_ok=True)
        raise RuntimeError(f"macOS speech synthesis failed: {completed.stderr[-500:]}")
    return _encode_audio(aiff_path, output_path)


def _sapi_synthesize(text: str, output_path: Path) -> Path:
    """Windows SAPI → WAV → ffmpeg MP3. Last-resort local fallback."""
    import tempfile
    import uuid

    wav_path = Path(tempfile.gettempdir()) / f"c2video-{uuid.uuid4().hex}.wav"
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "foreach ($v in $s.GetInstalledVoices()) { "
        "  if ($v.VoiceInfo.Culture.Name -eq 'zh-CN') { "
        "    $s.SelectVoice($v.VoiceInfo.Name); break "
        "  } "
        "}; "
        "$s.Rate = 1; "
        f"$s.SetOutputToWaveFile('{wav_path.as_posix()}'); "
        "$s.Speak([Console]::In.ReadToEnd()); "
        "$s.Dispose();"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        input=text,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0 or not wav_path.exists() or wav_path.stat().st_size < 100:
        raise RuntimeError(
            "Edge TTS failed and Windows SAPI fallback also failed. "
            f"SAPI stderr: {completed.stderr[-500:]}"
        )
    return _encode_audio(wav_path, output_path)


class EdgeTTSProvider(TTSProvider):
    """TTS provider backed by the Edge free TTS service."""

    def __init__(self, config: TTSConfig) -> None:
        self.config = config

    async def synthesize(
        self, text: str, output_path: Path, **kwargs
    ) -> Path:
        """Convert text to speech using Edge TTS.

        Args:
            text: Narration text.
            output_path: Destination MP3 path.
        """
        voice = kwargs.get("voice", self.config.voice)
        rate = kwargs.get("rate", self.config.rate)
        volume = kwargs.get("volume", self.config.volume)
        pitch = kwargs.get("pitch", self.config.pitch)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                output_path.unlink(missing_ok=True)
                communicate = edge_tts.Communicate(
                    text=text,
                    voice=voice,
                    rate=rate,
                    volume=volume,
                    pitch=pitch,
                )
                await communicate.save(str(output_path))
                if output_path.exists() and output_path.stat().st_size > 0:
                    return output_path
            except Exception as exc:  # noqa: BLE001 — retry then fall back
                last_error = exc
                await asyncio.sleep(0.8 * (2**attempt))

        if sys.platform == "darwin":
            try:
                return await asyncio.to_thread(_macos_synthesize, text, output_path)
            except Exception as macos_exc:
                raise RuntimeError(
                    f"Edge TTS failed ({last_error}) and macOS speech fallback "
                    f"also failed ({macos_exc})."
                ) from macos_exc

        if sys.platform == "win32":
            try:
                return await asyncio.to_thread(_sapi_synthesize, text, output_path)
            except Exception as sapi_exc:
                raise RuntimeError(
                    f"Edge TTS failed ({last_error}) and Windows SAPI fallback "
                    f"also failed ({sapi_exc}). Configure [tts] API if you have "
                    "a speech endpoint."
                ) from sapi_exc
        raise RuntimeError(f"Edge TTS failed: {last_error}")

