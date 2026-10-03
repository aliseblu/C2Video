"""Durable single-host queue. One queued/running job per Run, with fenced updates."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import uuid4

from c2video.capacity import CapacityExceeded, queue_limit

if TYPE_CHECKING:
    from c2video.storage.run_store import RunStore

JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_jobs (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    requested_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    heartbeat_at REAL,
    claim_token TEXT,
    worker_pid INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_requested ON run_jobs(status, requested_at);
CREATE TABLE IF NOT EXISTS worker_status (
    worker_id TEXT PRIMARY KEY,
    heartbeat_at REAL NOT NULL,
    concurrency INTEGER NOT NULL
);
"""
TERMINAL = {"COMPLETE", "FAILED", "CANCELED"}


class RunQueue:
    def __init__(self, store: RunStore) -> None:
        self.store = store

    def enqueue(self, run_id: str) -> dict:
        with self.store.atomic() as db:
            run = self.store.get_run(run_id)
            if not run:
                raise KeyError("Run not found")
            existing = db.execute("SELECT * FROM run_jobs WHERE run_id=?", (run_id,)).fetchone()
            if run["state"] in TERMINAL:
                raise ValueError("任务已结束；失败任务请先重试，完成或取消的任务请新建。")
            if run["is_paused"]:
                raise ValueError("任务已暂停，请先恢复。")
            if any(t["status"] == "waiting_human" for t in self.store.get_tasks(run_id)):
                raise ValueError("任务正在等待审核，请先通过或拒绝。")
            if existing and existing["status"] in {"queued", "running"}:
                if existing["status"] == "running":
                    # Remember starts racing with an exiting gate/pause worker.
                    db.execute("UPDATE run_jobs SET requested_at=? WHERE run_id=?",
                               (time.time(), run_id))
                return dict(existing)
            # Count and admit in the same write transaction: concurrent starts cannot overfill.
            pending = db.execute("SELECT COUNT(*) FROM run_jobs WHERE status='queued'").fetchone()[0]
            if pending >= queue_limit():
                raise CapacityExceeded("待执行队列已满，请等待现有任务完成后重试。")
            db.execute(
                """INSERT INTO run_jobs(run_id,status,requested_at) VALUES (?,'queued',?)
                ON CONFLICT(run_id) DO UPDATE SET status='queued',requested_at=excluded.requested_at,
                started_at=NULL,finished_at=NULL,heartbeat_at=NULL,claim_token=NULL,
                worker_pid=NULL,error=NULL""", (run_id, time.time()),
            )
            return dict(db.execute("SELECT * FROM run_jobs WHERE run_id=?", (run_id,)).fetchone())

    def claim(self) -> dict | None:
        with self.store.atomic() as db:
            # A queued task can be canceled or paused before a worker reaches it.
            db.execute("""UPDATE run_jobs SET status='idle',finished_at=?
                WHERE status='queued' AND run_id IN (
                    SELECT run_id FROM runs WHERE state IN ('COMPLETE','FAILED','CANCELED')
                    OR is_paused=1)""", (time.time(),))
            row = db.execute("SELECT * FROM run_jobs WHERE status='queued' ORDER BY requested_at LIMIT 1").fetchone()
            if not row:
                return None
            token = uuid4().hex
            now = time.time()
            db.execute("""UPDATE run_jobs SET status='running',claim_token=?,started_at=?,
                heartbeat_at=? WHERE run_id=? AND status='queued'""",
                (token, now, now, row["run_id"]))
            return dict(db.execute("SELECT * FROM run_jobs WHERE run_id=?", (row["run_id"],)).fetchone())

    def started(self, run_id: str, token: str, pid: int) -> None:
        with self.store.transaction() as db:
            db.execute("""UPDATE run_jobs SET worker_pid=?,heartbeat_at=?
                WHERE run_id=? AND claim_token=? AND status='running'""",
                (pid, time.time(), run_id, token))

    def finish(self, run_id: str, token: str, *, error: str | None = None) -> bool:
        with self.store.atomic() as db:
            job = db.execute("SELECT * FROM run_jobs WHERE run_id=? AND claim_token=? AND status='running'",
                             (run_id, token)).fetchone()
            if not job:
                return False
            run = self.store.get_run(run_id)
            resubmit = bool(not error and job["requested_at"] > job["started_at"]
                            and run and run["state"] not in TERMINAL and not run["is_paused"]
                            and not any(t["status"] == "waiting_human" for t in self.store.get_tasks(run_id)))
            status = "failed" if error else "queued" if resubmit else "idle"
            db.execute("UPDATE run_jobs SET status=?,finished_at=?,error=? WHERE run_id=? AND claim_token=?",
                       (status, time.time(), error, run_id, token))
            return True

    def heartbeat(self, worker_id: str, concurrency: int) -> None:
        with self.store.transaction() as db:
            db.execute("""INSERT INTO worker_status VALUES (?,?,?)
                ON CONFLICT(worker_id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at,
                concurrency=excluded.concurrency""", (worker_id, time.time(), concurrency))

    def running(self) -> list[dict]:
        with self.store.transaction() as db:
            return [dict(row) for row in db.execute("SELECT * FROM run_jobs WHERE status='running'")]

    def status(self) -> dict:
        with self.store.transaction() as db:
            counts = {row["status"]: row["n"] for row in db.execute(
                "SELECT status,COUNT(*) n FROM run_jobs GROUP BY status")}
            workers = [dict(row) for row in db.execute(
                "SELECT worker_id,heartbeat_at,concurrency FROM worker_status WHERE heartbeat_at>?",
                (time.time() - 10,))]
            recent = [dict(row) for row in db.execute(
                """SELECT run_id,status,requested_at,started_at,finished_at,worker_pid,error
                FROM run_jobs ORDER BY requested_at DESC LIMIT 30""")]
        return {"counts": counts, "workers": workers, "recent": recent,
                "worker_ready": bool(workers), "scope": "single-host"}
