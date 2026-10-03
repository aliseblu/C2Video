"""Single-host reliability: deterministic faults, no network or paid model calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from c2video.agent.locking import RunBusy, run_lock
from c2video.agent.runtime import AgentRuntime
from c2video.domain.models import ContentGoal, PlanTask, RunPlan, RunState, TaskStatus
from c2video.storage.jobs import RunQueue
from c2video.storage.run_store import RunStore
from c2video.tools.base import AgentTool, ToolResult
from c2video.tools.registry import ToolRegistry


class WaitingTool(AgentTool):
    name = "test.wait"

    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def execute(self, context):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return ToolResult(summary="done")


def setup(tmp_path, count=1):
    store = RunStore(tmp_path / "state.db")
    registry = ToolRegistry()
    tool = WaitingTool()
    registry.register(tool)
    runtime = AgentRuntime(store, registry, work_dir=str(tmp_path))
    goal = ContentGoal(run_id="run_test", query="Test", autonomy="auto")
    tasks = [PlanTask(task_type="test", target_state=RunState.PRODUCE,
                      tool_name=tool.name, max_attempts=1) for _ in range(count)]
    for index in range(1, count):
        tasks[index].depends_on = [tasks[index - 1].task_id]
    runtime.create(goal, RunPlan(run_id=goal.run_id, format="news_recap",
                                tasks=tasks, decision_summary="test"))
    return runtime, tool, store


@pytest.mark.asyncio
async def test_two_executors_cannot_run_same_tool(tmp_path):
    runtime, tool, store = setup(tmp_path)
    first = asyncio.create_task(runtime.run("run_test"))
    await tool.entered.wait()
    with pytest.raises(RunBusy):
        await runtime.run("run_test")
    tool.release.set()
    assert (await first)["run"]["state"] == "COMPLETE"
    assert tool.calls == 1
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_cancel_cannot_be_overwritten_by_late_success(tmp_path):
    runtime, tool, store = setup(tmp_path, count=2)
    running = asyncio.create_task(runtime.run("run_test"))
    await tool.entered.wait()
    runtime.cancel("run_test")
    tool.release.set()
    snapshot = await running
    assert snapshot["run"]["state"] == "CANCELED"
    assert all(t["status"] == "canceled" for t in snapshot["tasks"])
    assert tool.calls == 1
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 0
    assert not any(e["event_type"] == "run.completed" for e in snapshot["events"])
    with pytest.raises(ValueError):
        runtime.retry("run_test")
    store.set_run_state("run_test", RunState.COMPLETE)
    assert store.get_run("run_test")["state"] == "CANCELED"


@pytest.mark.asyncio
async def test_pause_finishes_current_step_then_resume_skips_it(tmp_path):
    runtime, tool, store = setup(tmp_path, count=2)
    running = asyncio.create_task(runtime.run("run_test"))
    await tool.entered.wait()
    runtime.pause("run_test")
    tool.release.set()
    snapshot = await running
    assert [t["status"] for t in snapshot["tasks"]] == ["succeeded", "pending"]
    runtime.resume("run_test")
    assert (await runtime.run("run_test"))["run"]["state"] == "COMPLETE"
    assert tool.calls == 2


@pytest.mark.asyncio
async def test_crash_marker_requires_explicit_retry(tmp_path):
    runtime, tool, store = setup(tmp_path)
    task = store.get_tasks("run_test")[0]
    store.set_task_status(task["task_id"], TaskStatus.RUNNING, attempt=1)
    snapshot = await runtime.run("run_test")
    assert snapshot["run"]["state"] == "FAILED"
    assert tool.calls == 0
    runtime.retry("run_test")
    tool.release.set()
    snapshot = await runtime.run("run_test")
    assert snapshot["run"]["state"] == "COMPLETE"
    assert snapshot["tasks"][0]["attempt"] == 2


def test_queue_submission_is_idempotent_under_concurrency(tmp_path):
    _, _, store = setup(tmp_path)
    queue = RunQueue(store)
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(lambda _: queue.enqueue("run_test"), range(16)))
    assert {j["run_id"] for j in jobs} == {"run_test"}
    assert queue.status()["counts"]["queued"] == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(lambda _: queue.claim(), range(4)))
    jobs = [j for j in claimed if j]
    assert len(jobs) == 1
    assert not queue.finish("run_test", "stale-token")
    assert queue.finish("run_test", jobs[0]["claim_token"])
    assert queue.status()["counts"]["idle"] == 1


def test_queued_cancel_never_claimed(tmp_path):
    runtime, _, store = setup(tmp_path)
    queue = RunQueue(store)
    queue.enqueue("run_test")
    runtime.cancel("run_test")
    assert queue.claim() is None


def test_atomic_rolls_back_state_and_event(tmp_path):
    _, _, store = setup(tmp_path)
    count = len(store.list_events("run_test"))
    with pytest.raises(RuntimeError):
        with store.atomic():
            store.set_run_state("run_test", RunState.PRODUCE)
            store.set_paused("run_test", True)
            raise RuntimeError("fault before commit")
    assert store.get_run("run_test")["state"] == "PLAN"
    assert store.get_run("run_test")["is_paused"] == 0
    assert len(store.list_events("run_test")) == count


def test_lock_is_released_on_exception(tmp_path):
    with pytest.raises(ValueError):
        with run_lock(tmp_path / "state.db", "run_test"):
            raise ValueError("fault")
    with run_lock(tmp_path / "state.db", "run_test"):
        pass


def test_trace_export_failure_does_not_fail_database_commit(tmp_path, monkeypatch):
    runtime, _, store = setup(tmp_path)
    original = Path.open

    def fail_trace(self, *args, **kwargs):
        if self.suffix == ".jsonl":
            raise OSError("disk full")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_trace)
    runtime.pause("run_test")
    assert store.get_run("run_test")["is_paused"] == 1
    assert store.list_events("run_test")[-1]["event_type"] == "run.paused"
