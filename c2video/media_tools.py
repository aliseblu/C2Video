"""Resolve capable media binaries without depending on an IDE's inherited PATH."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REQUIRED_FILTERS = frozenset({"subtitles", "drawtext"})
FALLBACK_BIN_DIRS = (
    Path("/opt/homebrew/opt/ffmpeg-full/bin"),
    Path("/usr/local/opt/ffmpeg-full/bin"),
)


class MediaToolsError(RuntimeError):
    """Missing executable or missing filters, with an actionable setup message."""


@dataclass(frozen=True)
class MediaTools:
    ffmpeg: str
    ffprobe: str


def _executable(value: str) -> str | None:
    found = shutil.which(str(Path(value).expanduser()))
    return str(Path(found).resolve()) if found else None


@lru_cache(maxsize=32)
def _inspect(binary: str, kind: str, mtime_ns: int, size: int) -> frozenset[str]:
    # The file fingerprint invalidates cached capabilities after binary upgrades.
    args = [binary, "-hide_banner", "-filters"] if kind == "ffmpeg" else [binary, "-version"]
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaToolsError(f"{binary} 无法运行或检测超时") from exc
    if result.returncode:
        raise MediaToolsError(f"{binary} 检测失败（退出码 {result.returncode}）")
    if kind == "ffmpeg":
        return frozenset(parts[1] for line in result.stdout.splitlines()
                         if len(parts := line.split()) >= 2)
    elif "ffprobe version" not in result.stdout:
        raise MediaToolsError(f"{binary} 不是可识别的 ffprobe")
    return frozenset()


def _validate(binary: str, kind: str) -> None:
    try:
        stat = Path(binary).stat()
    except OSError as exc:
        raise MediaToolsError(f"{binary} 不可读取") from exc
    available = _inspect(binary, kind, stat.st_mtime_ns, stat.st_size)
    if kind == "ffmpeg" and (missing := REQUIRED_FILTERS - available):
        raise MediaToolsError(f"{binary} 缺少滤镜：{', '.join(sorted(missing))}")


def resolve_media_tools() -> MediaTools:
    """Prefer a capable PATH binary; fall back to installed Homebrew full builds.

    Explicit C2VIDEO_FFMPEG / C2VIDEO_FFPROBE overrides fail clearly when invalid.
    No environment changes, package installation, shell invocation, or symlink writes.
    """
    override = os.environ.get("C2VIDEO_FFMPEG", "").strip()
    probe_override = os.environ.get("C2VIDEO_FFPROBE", "").strip()
    explicit_probe = _executable(probe_override) if probe_override else None
    if probe_override and not explicit_probe:
        raise MediaToolsError("C2VIDEO_FFPROBE 指定的文件不存在或不可执行。")
    if explicit_probe:
        _validate(explicit_probe, "ffprobe")
    choices = [override] if override else ["ffmpeg", *[
        str(directory / "ffmpeg") for directory in FALLBACK_BIN_DIRS
    ]]
    seen: set[str] = set()
    failures: list[str] = []
    for choice in choices:
        binary = _executable(choice)
        if not binary:
            if override:
                failures.append("C2VIDEO_FFMPEG 指定的文件不存在或不可执行")
            continue
        if binary in seen:
            continue
        seen.add(binary)
        try:
            _validate(binary, "ffmpeg")
            probe = explicit_probe
            if probe is None:
                name = "ffprobe.exe" if binary.lower().endswith(".exe") else "ffprobe"
                probe = _executable(str(Path(binary).with_name(name))) or _executable("ffprobe")
            if probe is None:
                raise MediaToolsError(f"{binary} 找不到配套的 ffprobe")
            _validate(probe, "ffprobe")
            return MediaTools(ffmpeg=binary, ffprobe=probe)
        except MediaToolsError as exc:
            failures.append(str(exc))
    detail = "；".join(failures) or "没有找到可执行的 ffmpeg/ffprobe"
    raise MediaToolsError(
        "视频环境不可用：" + detail + "。请安装支持 subtitles/drawtext 的完整版 FFmpeg"
        "（macOS 可安装 ffmpeg-full），或用 C2VIDEO_FFMPEG 指定其路径。"
    )


def ffmpeg_path() -> str:
    return resolve_media_tools().ffmpeg


def ffprobe_path() -> str:
    return resolve_media_tools().ffprobe


def media_tools_status() -> dict:
    try:
        tools = resolve_media_tools()
    except MediaToolsError as exc:
        return {"ok": False, "ffmpeg": None, "ffprobe": None, "detail": str(exc)}
    return {"ok": True, "ffmpeg": tools.ffmpeg, "ffprobe": tools.ffprobe,
            "detail": f"字幕/文字滤镜已通过 · {tools.ffmpeg}"}

