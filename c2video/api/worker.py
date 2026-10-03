"""Independent workers and the durable queue supervisor. No silent config fallback."""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import threading

from c2video.agent.supervisor import Supervisor, parent_watchdog
from c2video.application import ApplicationService
from c2video.config.loader import load_config


async def serve(service, concurrency: int) -> None:
    supervisor = Supervisor(service, concurrency=concurrency)
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, supervisor.stopping.set)
    await supervisor.run()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--claim-token")
    parser.add_argument("--supervisor-pid", type=int)
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    service = ApplicationService(work_dir=args.work_dir, db_path=args.db, config=load_config())
    if args.daemon:
        asyncio.run(serve(service, args.concurrency))
        return
    if not args.run_id:
        parser.error("--run-id is required unless --daemon is used")
    if args.claim_token:
        with service.store.transaction() as db:
            job = db.execute("SELECT * FROM run_jobs WHERE run_id=?", (args.run_id,)).fetchone()
        if not job or job["status"] != "running" or job["claim_token"] != args.claim_token:
            raise SystemExit("Job claim is no longer valid")
    stop = threading.Event()
    if args.supervisor_pid:
        if os.getpid() != os.getpgrp():
            raise SystemExit("Supervised workers must own their process group")
        threading.Thread(target=parent_watchdog,
                         args=(service.store, args.run_id, args.supervisor_pid, stop),
                         daemon=True).start()
    try:
        service.execute_sync(args.run_id)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
