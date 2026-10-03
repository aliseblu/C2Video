"""Single-host admission controls, not a provider billing or disk quota system."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


class CapacityExceeded(RuntimeError):
    """Request may be retried once capacity has recovered."""


def queue_limit() -> int:
    limit = int(os.environ.get("C2VIDEO_MAX_QUEUED_RUNS", "100"))
    if not 1 <= limit <= 10_000:
        raise ValueError("C2VIDEO_MAX_QUEUED_RUNS must be between 1 and 10000")
    return limit


def minimum_free_bytes(*, production: bool) -> int:
    minimum = int(os.environ.get("C2VIDEO_MIN_FREE_DISK_MB", "1024" if production else "0"))
    if minimum < 0 or (production and minimum < 128):
        raise ValueError("Minimum free disk must be nonnegative; production requires >=128 MB")
    return minimum * 1024 * 1024


def ensure_disk_capacity(work_dir: str, *, minimum_bytes: int) -> None:
    target = Path(work_dir).resolve()
    # Programmatic callers may keep the DB elsewhere before the work directory exists.
    while not target.exists():
        target = target.parent
    if minimum_bytes and shutil.disk_usage(target).free < minimum_bytes:
        raise CapacityExceeded("可用磁盘空间不足，已暂停接收新任务；请备份并归档后重试。")


