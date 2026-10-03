"""Capability-aware binary selection with a deliberately unsuitable default PATH."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from c2video import media_tools
from c2video.api.app import create_app


@pytest.fixture
def binaries(tmp_path, monkeypatch):
    normal = tmp_path / "normal"
    full = tmp_path / "full"
    for directory in (normal, full):
        directory.mkdir()
        for name in ("ffmpeg", "ffprobe"):
            binary = directory / name
            binary.write_text("fake executable")
            binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(normal))
    monkeypatch.delenv("C2VIDEO_FFMPEG", raising=False)
    monkeypatch.delenv("C2VIDEO_FFPROBE", raising=False)
    monkeypatch.setattr(media_tools, "FALLBACK_BIN_DIRS", (full,))
    media_tools._inspect.cache_clear()
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] == 5
        if args[-1] == "-version":
            return SimpleNamespace(returncode=0, stdout="ffprobe version test", stderr="")
        filters = " .. scale V->V scale\n"
        if Path(args[0]).parent == full:
            filters += " .. subtitles V->V subtitles\n T. drawtext V->V text\n"
        return SimpleNamespace(returncode=0, stdout=filters, stderr="")

    monkeypatch.setattr(media_tools.subprocess, "run", fake_run)
    yield normal, full, calls
    media_tools._inspect.cache_clear()


def test_incapable_path_binary_falls_back_to_full_pair(binaries):
    normal, full, calls = binaries
    tools = media_tools.resolve_media_tools()
    assert tools.ffmpeg == str(full / "ffmpeg")
    assert tools.ffprobe == str(full / "ffprobe")
    assert calls[0][0] == str(normal / "ffmpeg")


def test_capable_path_has_priority(binaries, monkeypatch):
    _, full, _ = binaries
    monkeypatch.setenv("PATH", str(full))
    monkeypatch.setattr(media_tools, "FALLBACK_BIN_DIRS", ())
    assert media_tools.ffmpeg_path() == str(full / "ffmpeg")


def test_installed_full_build_works_without_media_on_path(binaries, monkeypatch):
    _, full, _ = binaries
    monkeypatch.setenv("PATH", "")
    assert media_tools.ffprobe_path() == str(full / "ffprobe")


def test_explicit_incapable_binary_is_not_silently_ignored(binaries, monkeypatch):
    normal, _, _ = binaries
    monkeypatch.setenv("C2VIDEO_FFMPEG", str(normal / "ffmpeg"))
    with pytest.raises(media_tools.MediaToolsError, match="缺少滤镜"):
        media_tools.resolve_media_tools()


def test_explicit_full_binary_and_probe_are_used(binaries, monkeypatch):
    normal, full, _ = binaries
    monkeypatch.setenv("C2VIDEO_FFMPEG", str(full / "ffmpeg"))
    monkeypatch.setenv("C2VIDEO_FFPROBE", str(normal / "ffprobe"))
    assert media_tools.resolve_media_tools().ffprobe == str(normal / "ffprobe")


@pytest.mark.parametrize("key", ["C2VIDEO_FFMPEG", "C2VIDEO_FFPROBE"])
def test_nonexistent_override_is_reported(binaries, monkeypatch, key):
    monkeypatch.setenv(key, "/missing/tool")
    with pytest.raises(media_tools.MediaToolsError, match=key):
        media_tools.resolve_media_tools()


def test_capabilities_are_cached_and_upgrade_invalidates_cache(binaries):
    _, full, calls = binaries
    media_tools.resolve_media_tools()
    count = len(calls)
    media_tools.resolve_media_tools()
    assert len(calls) == count
    (full / "ffmpeg").write_text("different file size after upgrade")
    media_tools.resolve_media_tools()
    assert len(calls) == count + 1


def test_no_capable_build_gives_actionable_error(binaries, monkeypatch):
    monkeypatch.setattr(media_tools, "FALLBACK_BIN_DIRS", ())
    status = media_tools.media_tools_status()
    assert status["ok"] is False and status["ffmpeg"] is None
    assert "subtitles" in status["detail"] and "ffmpeg-full" in status["detail"]


def test_probe_must_be_available(binaries, monkeypatch):
    normal, full, _ = binaries
    (normal / "ffprobe").unlink()
    (full / "ffprobe").unlink()
    with pytest.raises(media_tools.MediaToolsError, match="ffprobe"):
        media_tools.resolve_media_tools()


def test_probe_timeout_is_reported(binaries, monkeypatch):
    def timeout(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    monkeypatch.setattr(media_tools.subprocess, "run", timeout)
    assert media_tools.media_tools_status()["ok"] is False


def test_health_reports_selected_binary_and_missing_filters(binaries, tmp_path, monkeypatch):
    _, full, _ = binaries
    client = TestClient(create_app(work_dir=str(tmp_path / "work")))
    status = client.get("/api/health").json()
    assert status["checks"]["ffmpeg"] is True
    assert status["ffmpeg_path"] == str(full / "ffmpeg")
    monkeypatch.setattr(media_tools, "FALLBACK_BIN_DIRS", ())
    status = client.get("/api/health").json()
    assert status["checks"]["ffmpeg"] is False
    assert "缺少滤镜" in status["ffmpeg_detail"]
