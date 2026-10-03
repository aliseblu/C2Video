"""Real isolated worker processes and deterministic failure injection; no paid calls."""
import asyncio
import os
import signal
import subprocess
import sys
import time

import pytest

from c2video.agent.supervisor import Child, Supervisor
from c2video.application import ApplicationService
from c2video.domain.models import ContentGoal, PlanTask, RunPlan, RunState


def service_with_plan(tmp_path, *, gate=False):
    service = ApplicationService(work_dir=str(tmp_path))
    goal = ContentGoal(run_id="run_worker", query="worker test", autonomy="supervised")
    tasks = [PlanTask(task_type="discover", target_state=RunState.DISCOVER,
                      tool_name="content.discover", max_attempts=1)]
    if gate:
        tasks.append(PlanTask(task_type="gate_1", target_state=RunState.WAIT_GATE_1,
                              human_gate=True, depends_on=[tasks[0].task_id]))
    service.runtime.create(goal, RunPlan(run_id=goal.run_id, format="news_recap",
                                        tasks=tasks, decision_summary="test"))
    return service


async def settle(supervisor, predicate, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        await supervisor.tick()
        if predicate():
            return
        await asyncio.sleep(.05)
    raise AssertionError("Worker did not settle")


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", [False, True])
async def test_real_queued_worker_checkpoint_and_gate(tmp_path, gate):
    service = service_with_plan(tmp_path, gate=gate)
    service.start_worker("run_worker")
    supervisor = Supervisor(service)
    try:
        await settle(supervisor, lambda: not supervisor.children and
                     supervisor.queue.status()["counts"].get("idle") == 1)
        snapshot = service.get_run("run_worker")
        assert snapshot["run"]["state"] == ("WAIT_GATE_1" if gate else "COMPLETE")
        assert snapshot["tasks"][0]["status"] == "succeeded"
        assert not supervisor.queue.running()
    finally:
        for child in supervisor.children.values():
            await supervisor.terminate(child)


@pytest.mark.asyncio
async def test_stale_inflight_job_fails_without_replay(tmp_path):
    service = service_with_plan(tmp_path)
    service.start_worker("run_worker")
    supervisor = Supervisor(service)
    supervisor.queue.claim()  # Simulates death between claim and Popen.
    await supervisor.tick()
    assert service.get_run("run_worker")["run"]["state"] == "FAILED"
    assert not supervisor.children
    assert supervisor.queue.status()["counts"]["failed"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["cancel", "timeout", "shutdown"])
async def test_owned_process_group_is_stopped(tmp_path, reason):
    service = service_with_plan(tmp_path)
    service.start_worker("run_worker")
    supervisor = Supervisor(service)
    job = supervisor.queue.claim()
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                               start_new_session=True)
    child = Child(process, job, time.monotonic() + (0 if reason == "timeout" else 30))
    supervisor.children["run_worker"] = child
    try:
        if reason == "cancel":
            service.runtime.cancel("run_worker")
        if reason == "shutdown":
            supervisor.stopping.set()
            await supervisor.run()
        else:
            await supervisor.tick()
        assert process.poll() is not None
        assert not supervisor.children
        assert service.store.get_run("run_worker")["state"] == (
            "CANCELED" if reason == "cancel" else "FAILED")
    finally:
        await supervisor.terminate(child)


@pytest.mark.asyncio
async def test_termination_also_stops_owned_grandchild(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"Path({str(pid_file)!r}).write_text(str(p.pid)); time.sleep(60)"
    )
    process = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
    child = Child(process, {}, time.monotonic() + 30)
    try:
        for _ in range(100):
            if pid_file.exists():
                break
            await asyncio.sleep(.02)
        assert pid_file.exists()
        grandchild = int(pid_file.read_text())
        await Supervisor.terminate(child)
        for _ in range(100):
            status = subprocess.run(["ps", "-o", "stat=", "-p", str(grandchild)],
                                    capture_output=True, text=True).stdout.strip()
            if not status or status.startswith("Z"):
                break
            await asyncio.sleep(.02)
        assert not status or status.startswith("Z")
    finally:
        await Supervisor.terminate(child)


def test_orphan_watchdog_exits_its_own_process_group(tmp_path):
    service = service_with_plan(tmp_path)
    code = (
        "import threading; from c2video.agent.supervisor import parent_watchdog; "
        "from c2video.storage.run_store import RunStore; "
        f"parent_watchdog(RunStore({service.db_path!r}), 'run_worker', -1, threading.Event())"
    )
    process = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
    try:
        assert process.wait(timeout=8) == -signal.SIGTERM
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
