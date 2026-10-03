"""Single-host execution locks. Keep lock files: unlinking can break exclusion."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


class RunBusy(RuntimeError):
    pass


class ProcessLock:
    """OS releases the lock on process death; requires local POSIX storage."""

    def __init__(self, directory: Path, key: str, *, blocking: bool = False) -> None:
        self.path = directory / (hashlib.sha256(key.encode()).hexdigest() + ".lock")
        self.fd: int | None = None
        self.blocking = blocking

    def __enter__(self):
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if self.blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            os.close(fd)
            raise RunBusy("该任务仍有执行者，请等待当前执行结束。") from exc
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd
        return self

    def __exit__(self, *_args) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def run_lock(db_path: str | Path, run_id: str) -> ProcessLock:
    path = Path(db_path).resolve()
    return ProcessLock(path.parent / ".execution-locks", str(path) + ":" + run_id)
