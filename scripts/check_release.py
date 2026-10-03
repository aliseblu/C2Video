"""Reject accidental secrets and local output in the Git snapshot, without printing values."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import PurePosixPath

BLOCKED_DIRS = {".venv", "node_modules", ".idea", ".run", ".codex", ".agents", "work", "final",
                "artifacts", "__pycache__", ".pytest_cache", ".ruff_cache"}
BLOCKED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".docx"}
PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(rb"sk-[A-Za-z0-9_-]{24,}"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args()
    paths = subprocess.check_output(["git", "ls-files", "-z"]).split(b"\0")
    failures = []
    total = 0
    for raw in paths:
        if not raw:
            continue
        path = raw.decode()
        parts = PurePosixPath(path)
        example = parts.name.endswith(".example")
        if (set(parts.parts) & BLOCKED_DIRS or parts.suffix in BLOCKED_SUFFIXES
                or (parts.name.startswith(".env") and not example)
                or (parts.name.endswith(".env") and not example)
                or parts.name == "c2video.toml"):
            failures.append(f"{path}: private/local file")
            continue
        # Scan Git objects, not a potentially different local working tree.
        ref = ":" + path if args.staged else "HEAD:" + path
        data = subprocess.check_output(["git", "show", ref])
        total += 1
        if len(data) > 5_000_000:
            failures.append(f"{path}: unexpectedly large file")
        if any(pattern.search(data) for pattern in PATTERNS):
            failures.append(f"{path}: possible credential (value hidden)")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"Release snapshot check passed: {total} files; no forbidden paths or recognized key patterns.")
    print("This heuristic complements review; it is not a guarantee that all secrets are detected.")


if __name__ == "__main__":
    main()
