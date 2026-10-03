"""Bounded single-host subprocess supervisor backed by RunQueue.

Queued work survives restart. Ambiguous in-flight work becomes a visible failure,
never an automatic replay of potentially paid side effects.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from c2video.agent.locking import ProcessLock, RunBusy, run_lock
from c2video.storage.jobs import RunQueue
from c2video.storage.run_store import RunStore

logger = logging.getLogger(__name__)


@dataclass
class Child:
    process: subprocess.Popen
    job: dict
    deadline: float
    stop_reason: str | None = None


class Supervisor:
    def __init__(self, service, *, concurrency: int = 1, poll_seconds: float = 0.25):
        if not 1 <= concurrency <= 4:
            raise ValueError("Worker concurrency must be between 1 and 4")
        self.service = service
        self.queue = RunQueue(service.store)
        self.concurrency = concurrency
        self.poll_seconds = poll_seconds
        self.children: dict[str, Child] = {}
        self.worker_id = f"worker_{uuid4().hex}"
        self.stopping = asyncio.Event()

    async def run(self) -> None:
        db = Path(self.service.db_path).resolve()
        try:
            with ProcessLock(db.parent / ".execution-locks", str(db) + ":supervisor"):
                await self._serve()
        except RunBusy:
            # A separate worker process or another API instance already supervises this DB.
            return

    async def _serve(self) -> None:
        try:
            while not self.stopping.is_set():
                self.queue.heartbeat(self.worker_id, self.concurrency)
                await self.tick()
                try:
                    await asyncio.wait_for(self.stopping.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass
        finally:
            for child in list(self.children.values()):
                child.stop_reason = "Worker 服务关闭，执行中断；请检查后确认重试。"
                try:
                    await self.terminate(child)
                    self._finish(child)
                except Exception as exc:
                    logger.error("worker_shutdown_failed: %s", type(exc).__name__)
            self.children.clear()
            with self.service.store.transaction() as db:
                db.execute("DELETE FROM worker_status WHERE worker_id=?", (self.worker_id,))

    async def tick(self) -> None:
        for run_id, child in list(self.children.items()):
            run = self.service.store.get_run(run_id)
            if run and run["state"] == "CANCELED":
                child.stop_reason = "用户取消"
                await self.terminate(child)
            elif time.monotonic() >= child.deadline:
                child.stop_reason = "任务执行超时；请检查外部操作结果后确认重试。"
                await self.terminate(child)
            if child.process.poll() is not None:
                self._finish(child)
                del self.children[run_id]
        # After a crash, an orphan watchdog exits its process group. Until its Run
        # lock is released, do not touch state and never launch a second executor.
        for job in self.queue.running():
            if job["run_id"] in self.children:
                continue
            try:
                with run_lock(self.service.db_path, job["run_id"]):
                    run = self.service.store.get_run(job["run_id"])
                    error = None
                    if run and run["state"] not in {"COMPLETE", "FAILED", "CANCELED"}:
                        # A normally exiting child may have stopped at a persisted gate or pause.
                        waiting = any(t["status"] == "waiting_human"
                                      for t in self.service.store.get_tasks(job["run_id"]))
                        if not waiting and not run["is_paused"]:
                            error = "Worker 异常退出，操作结果可能未知；检查后人工确认重试。"
                            self.service.runtime.interrupt(job["run_id"], error)
                    self.queue.finish(job["run_id"], job["claim_token"], error=error)
            except RunBusy:
                continue
        while len(self.children) < self.concurrency and not self.stopping.is_set():
            job = self.queue.claim()
            if job is None:
                break
            try:
                run = self.service.store.get_run(job["run_id"])
                remaining = max(0.0, float(run["budget"]["max_runtime_seconds"])
                                - float(run["spent"].get("runtime_seconds", 0)))
                process = subprocess.Popen(
                    [sys.executable, "-m", "c2video.api.worker",
                     "--db", str(Path(self.service.db_path).resolve()),
                     "--work-dir", str(Path(self.service.work_dir).resolve()),
                     "--run-id", job["run_id"], "--claim-token", job["claim_token"],
                     "--supervisor-pid", str(os.getpid())],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                child = Child(process, job, time.monotonic() + remaining)
                self.children[job["run_id"]] = child
                self.queue.started(job["run_id"], job["claim_token"], process.pid)
            except Exception as exc:
                logger.error("worker_start_failed: %s", type(exc).__name__)
                if job["run_id"] in self.children:
                    child = self.children.pop(job["run_id"])
                    await self.terminate(child)
                try:
                    with run_lock(self.service.db_path, job["run_id"]):
                        self.service.runtime.interrupt(job["run_id"], "Worker 启动失败，请检查服务日志。")
                except RunBusy:
                    pass
                self.queue.finish(job["run_id"], job["claim_token"], error="Worker 启动失败")

    @staticmethod
    async def terminate(child: Child) -> None:
        # Only ever signal the process group created by this supervisor's Popen.
        if child.process.poll() is not None:
            return
        try:
            os.killpg(child.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.to_thread(child.process.wait, timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await asyncio.to_thread(child.process.wait, timeout=2)

    def _finish(self, child: Child) -> None:
        job = child.job
        try:
            with run_lock(self.service.db_path, job["run_id"]):
                run = self.service.store.get_run(job["run_id"])
                error = child.stop_reason
                if run and run["state"] == "CANCELED":
                    error = None
                elif child.process.returncode not in (0, None) or error:
                    error = error or "Worker 异常退出；检查产物后确认重试。"
                    self.service.runtime.interrupt(job["run_id"], error)
                elif run and run["state"] == "FAILED":
                    error = "任务执行失败，请查看任务事件。"
                self.queue.finish(job["run_id"], job["claim_token"], error=error)
        except RunBusy:
            # Another authorized CLI invocation owns the Run; recover later.
            pass


def parent_watchdog(store: RunStore, run_id: str, parent_pid: int, stop) -> None:
    """Independent thread also notices a supervisor killed without graceful shutdown."""
    while not stop.wait(0.5):
        orphaned = os.getppid() != parent_pid
        try:
            run = store.get_run(run_id)
            canceled = bool(run and run["state"] == "CANCELED")
        except Exception:
            canceled = False
        if orphaned or canceled:
            if os.getpid() == os.getpgrp():
                os.killpg(os.getpgrp(), signal.SIGTERM)
            return
