"""Predictable container/service entrypoints and non-mutating health probes."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.request import urlopen

from c2video.api.access import PlatformSettings


def worker_healthy(db_path: Path, *, now: float | None = None) -> bool:
    """Read the shared heartbeat without creating a missing database."""
    try:
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=2)) as db:
            row = db.execute("SELECT MAX(heartbeat_at) FROM worker_status").fetchone()
        age = (time.time() if now is None else now) - float(row[0])
        return 0 <= age < 10
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return False


def api_healthy() -> bool:
    try:
        with urlopen("http://127.0.0.1:8765/healthz", timeout=3) as response:
            return response.status == 200 and json.load(response).get("ok") is True
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("api", "worker", "health"))
    parser.add_argument("target", nargs="?", choices=("api", "worker"))
    args = parser.parse_args()
    work = Path(os.environ.get("C2VIDEO_WORK_DIR", "work"))
    if args.role == "health":
        if not args.target:
            parser.error("health requires api or worker")
        ok = api_healthy() if args.target == "api" else worker_healthy(work / "c2video-agent.db")
        raise SystemExit(0 if ok else 1)
    if args.target:
        parser.error("target is only supported by health")
    # Both processes validate the same access and worker configuration before work starts.
    settings = PlatformSettings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.role == "worker":
        import asyncio

        from c2video.api.worker import serve
        from c2video.application import ApplicationService
        from c2video.config.loader import load_config

        config = load_config()
        service = ApplicationService(work_dir=str(work), config=config)
        asyncio.run(serve(service, settings.concurrency))
    else:
        import uvicorn

        # No auto-port fallback, reload or multiple API workers in this deployment.
        # Access logs are disabled: Uvicorn's default includes raw query strings.
        uvicorn.run("c2video.api.app:app", host="0.0.0.0", port=8765, workers=1,
                    access_log=False, proxy_headers=True,
                    forwarded_allow_ips=os.environ.get("C2VIDEO_FORWARDED_ALLOW_IPS", "127.0.0.1"),
                    timeout_graceful_shutdown=20)


if __name__ == "__main__":
    main()
