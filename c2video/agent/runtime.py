"""Bounded task runner with durable state, retry, idempotency, gates, and budgets."""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

from c2video.agent.locking import run_lock
from c2video.domain.models import (
    ContentGoal,
    RunEvent,
    RunPlan,
    RunState,
    TaskStatus,
    ToolCall,
)
from c2video.storage.run_store import RunStore
from c2video.tools.base import ToolContext
from c2video.tools.registry import ToolRegistry


class BudgetExceeded(RuntimeError):
    pass


class AgentRuntime:
    def __init__(self, store: RunStore, registry: ToolRegistry, *, work_dir: str = "work") -> None:
        self.store = store
        self.registry = registry
        self.work_dir = work_dir

    def create(self, goal: ContentGoal, plan: RunPlan) -> str:
        seen: set[str] = set()
        for task in plan.tasks:
            if task.task_id in seen or not set(task.depends_on).issubset(seen):
                raise ValueError("Plan must have unique, ordered tasks with valid dependencies")
            if task.tool_name:
                self.registry.get(task.tool_name)
            seen.add(task.task_id)
        self.store.create_run(goal, plan)
        self.store.append_event(
            RunEvent(
                run_id=goal.run_id,
                event_type="run.created",
                state=RunState.PLAN,
                summary=plan.decision_summary,
                payload={"format": plan.format, "human_gates": plan.human_gates},
            )
        )
        return goal.run_id

    async def run(self, run_id: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        # Covers CLI, request-local runs, and independently spawned workers alike.
        with run_lock(self.store.path, run_id):
            return await self._run_locked(run_id, payload=payload)

    def _current(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise KeyError("Run not found")
        return run

    @staticmethod
    def _terminal(run: dict[str, Any]) -> bool:
        return run["state"] in {"COMPLETE", "FAILED", "CANCELED"}

    def interrupt(self, run_id: str, reason: str) -> None:
        """Caller must own the Run lock or have stopped its owned worker first."""
        with self.store.atomic():
            run = self._current(run_id)
            if self._terminal(run):
                return
            for task in self.store.get_tasks(run_id):
                if task["status"] == TaskStatus.RUNNING.value:
                    self.store.set_task_status(task["task_id"], TaskStatus.FAILED, error=reason)
            self.store.set_run_state(run_id, RunState.FAILED, error=reason)
            self._event(run_id, None, "run.interrupted", RunState.FAILED, reason)

    async def _run_locked(self, run_id: str, *, payload: dict[str, Any] | None) -> dict[str, Any]:
        snapshot = self.store.snapshot(run_id)
        if not snapshot:
            raise KeyError("Run not found")
        run = snapshot["run"]
        if self._terminal(run) or run.get("is_paused"):
            return snapshot
        # A leftover running step has an ambiguous external outcome. Never replay it silently.
        if any(task["status"] == "running" for task in snapshot["tasks"]):
            self.interrupt(run_id, "上次执行中断，外部操作结果可能未知。检查产物后人工确认重试。")
            return self.store.snapshot(run_id) or {}
        goal = ContentGoal.model_validate(snapshot["goal"])
        spent = dict(run.get("spent") or {})
        previous_seconds = float(spent.get("runtime_seconds", 0))
        started = time.monotonic()
        plan = RunPlan.model_validate(snapshot["plan"])
        dependencies = {task.task_id: task.depends_on for task in plan.tasks}

        while True:
            current = self._current(run_id)
            if self._terminal(current) or current.get("is_paused"):
                break
            tasks = self.store.get_tasks(run_id)
            if any(task["status"] in {"failed", "canceled"} for task in tasks):
                self.interrupt(run_id, "任务包含未处理的失败步骤，请先确认重试。")
                break
            next_task = self._next_task(tasks)
            if next_task is None:
                with self.store.atomic():
                    if not self._terminal(self._current(run_id)):
                        self.store.set_run_state(run_id, RunState.COMPLETE)
                        self._event(run_id, None, "run.completed", RunState.COMPLETE, "Run completed")
                break
            completed = {t["task_id"] for t in tasks if t["status"] in {"succeeded", "skipped"}}
            if not set(dependencies.get(next_task["task_id"], [])).issubset(completed):
                self.interrupt(run_id, "任务依赖未完成，停止执行。")
                break
            spent["runtime_seconds"] = previous_seconds + time.monotonic() - started
            try:
                self._check_budget(goal, spent, started)
            except BudgetExceeded:
                break
            task_id = next_task["task_id"]
            state = RunState(next_task["target_state"])
            if next_task["tool_name"] is None:
                if next_task["status"] == "waiting_human":
                    break
                with self.store.atomic():
                    if self._terminal(self._current(run_id)):
                        break
                    if self._gate_required(goal, state):
                        self.store.set_task_status(task_id, TaskStatus.WAITING_HUMAN)
                        self.store.set_run_state(run_id, state)
                        self._event(run_id, task_id, "gate.waiting", state,
                                    f"{state.value} requires approval", TaskStatus.WAITING_HUMAN)
                    else:
                        self.store.set_task_status(task_id, TaskStatus.SKIPPED)
                if self._gate_required(goal, state):
                    break
                continue

            attempt = int(next_task["attempt"] or 0) + 1
            with self.store.atomic():
                if self._terminal(self._current(run_id)) or self._current(run_id)["is_paused"]:
                    break
                self.store.set_run_state(run_id, state)
                self.store.set_task_status(task_id, TaskStatus.RUNNING, attempt=attempt)
                self._event(run_id, task_id, "task.started", state,
                            next_task["task_type"], TaskStatus.RUNNING)
            call_started = time.monotonic()
            try:
                tool = self.registry.get(next_task["tool_name"])
                result = await tool.execute(ToolContext(
                    run_id=run_id, task_id=task_id, work_dir=self.work_dir,
                    payload=dict(payload or {}),
                ))
            except asyncio.CancelledError:
                self.interrupt(run_id, "执行被中断；检查外部调用和产物后再确认重试。")
                raise
            except Exception as exc:
                with self.store.atomic():
                    if self._terminal(self._current(run_id)):
                        break
                    self.store.add_tool_call(ToolCall(
                        run_id=run_id, task_id=task_id, tool_name=next_task["tool_name"],
                        idempotency_key=next_task["idempotency_key"], status=TaskStatus.FAILED,
                        error=str(exc), attempt=attempt,
                        latency_ms=int((time.monotonic() - call_started) * 1000),
                    ))
                    spent["runtime_seconds"] = previous_seconds + time.monotonic() - started
                    self.store.update_spent(run_id, spent)
                    if attempt < int(next_task["max_attempts"]):
                        self.store.set_task_status(task_id, TaskStatus.PENDING, attempt=attempt, error=str(exc))
                        self._event(run_id, task_id, "task.retry", state, str(exc), TaskStatus.PENDING)
                    else:
                        self.store.set_task_status(task_id, TaskStatus.FAILED, attempt=attempt, error=str(exc))
                        self.store.set_run_state(run_id, RunState.FAILED, error=str(exc))
                        self._event(run_id, task_id, "task.failed", RunState.FAILED, str(exc), TaskStatus.FAILED)
                continue

            # Commit the successful result and checkpoint together, behind a terminal-state check.
            # Cancellation may have been written while the tool was running.
            with self.store.atomic():
                if self._terminal(self._current(run_id)):
                    break
                for artifact in result.artifacts:
                    self.store.add_artifact(artifact)
                latency = int((time.monotonic() - call_started) * 1000)
                self.store.add_tool_call(ToolCall(
                    run_id=run_id, task_id=task_id, tool_name=tool.name,
                    idempotency_key=next_task["idempotency_key"], status=TaskStatus.SUCCEEDED,
                    output_summary=result.summary, attempt=attempt, cost_usd=result.cost_usd,
                    latency_ms=latency,
                ))
                spent["llm_calls"] = int(spent.get("llm_calls", 0)) + result.llm_calls
                spent["cost_usd"] = float(spent.get("cost_usd", 0)) + result.cost_usd
                spent["runtime_seconds"] = round(previous_seconds + time.monotonic() - started, 3)
                self.store.update_spent(run_id, spent)
                self.store.set_task_status(task_id, TaskStatus.SUCCEEDED, attempt=attempt)
                self.store.append_event(RunEvent(
                    run_id=run_id, task_id=task_id, event_type="task.succeeded",
                    state=state, status=TaskStatus.SUCCEEDED, summary=result.summary,
                    payload=result.payload, cost_usd=result.cost_usd, latency_ms=latency,
                    output_artifact_ids=[a.artifact_id for a in result.artifacts],
                ))
        return self.store.snapshot(run_id) or {}

    def approve_gate(self, run_id: str, *, approved: bool, summary: str = "") -> None:
        with self.store.atomic():
            if self._terminal(self._current(run_id)):
                raise ValueError("已结束的任务不能审核。")
            waiting = next((t for t in self.store.get_tasks(run_id)
                            if t["status"] == "waiting_human"), None)
            if not waiting:
                raise ValueError("Run has no waiting Gate")
            state = RunState(waiting["target_state"])
            if approved:
                self.store.set_task_status(waiting["task_id"], TaskStatus.SUCCEEDED)
                self._event(run_id, waiting["task_id"], "gate.approved", state, summary or "Gate approved")
            else:
                self.cancel(run_id, summary=summary or "Gate rejected")

    def cancel(self, run_id: str, *, summary: str = "Canceled by user") -> None:
        with self.store.atomic():
            run = self._current(run_id)
            if run["state"] == "CANCELED":
                return
            if self._terminal(run):
                raise ValueError("任务已结束，不能再次取消。")
            self.store.set_run_state(run_id, RunState.CANCELED, error=summary)
            for task in self.store.get_tasks(run_id):
                if task["status"] in {"pending", "running", "waiting_human"}:
                    self.store.set_task_status(task["task_id"], TaskStatus.CANCELED)
            self._event(run_id, None, "run.canceled", RunState.CANCELED, summary)

    def pause(self, run_id: str) -> None:
        with self.store.atomic():
            run = self._current(run_id)
            if self._terminal(run):
                raise ValueError("任务已结束，不能暂停。")
            self.store.set_paused(run_id, True)
            self._event(run_id, None, "run.paused", RunState(run["state"]),
                        "已请求暂停，当前步骤完成后停止。")

    def resume(self, run_id: str) -> None:
        with self.store.atomic():
            run = self._current(run_id)
            if self._terminal(run):
                raise ValueError("任务已结束；失败任务请重试，其他情况请新建。")
            self.store.set_paused(run_id, False)
            self._event(run_id, None, "run.resumed", RunState(run["state"]), "Run resumed by user")

    def retry(self, run_id: str, *, task_id: str | None = None) -> str:
        with run_lock(self.store.path, run_id), self.store.atomic():
            run = self._current(run_id)
            if run["state"] != "FAILED":
                raise ValueError("仅失败任务可重试；完成或取消的任务请新建。")
            tasks = self.store.get_tasks(run_id)
            task = next((t for t in tasks if t["status"] == "failed"
                         and (task_id is None or t["task_id"] == task_id)), None)
            # Process death before a step starts has no failed task yet.
            if task is None and task_id is None:
                task = next((t for t in tasks if t["status"] == "pending"), None)
            if not task and task_id is None and tasks and all(
                t["status"] in {"succeeded", "skipped"} for t in tasks
            ):
                # Final checkpoint committed before process death; nothing to replay.
                self.store.set_run_state(run_id, RunState.COMPLETE, error=None, allow_retry=True)
                self._event(run_id, None, "run.completed", RunState.COMPLETE,
                            "人工确认恢复：全部步骤已提交，无需重复执行。")
                return tasks[-1]["task_id"]
            if not task:
                raise ValueError("No failed task available to retry")
            self.store.set_run_state(run_id, RunState(task["target_state"]), error=None, allow_retry=True)
            self.store.set_paused(run_id, False)
            self.store.set_task_status(task["task_id"], TaskStatus.PENDING, error=None)
            self._event(run_id, task["task_id"], "task.retry_requested",
                        RunState(task["target_state"]), "用户确认重试；外部副作用可能重复。",
                        TaskStatus.PENDING)
            return task["task_id"]

    def replay(self, run_id: str) -> dict[str, Any]:
        snapshot = self.store.snapshot(run_id)
        if not snapshot:
            raise KeyError(f"Run not found: {run_id}")
        return {
            "run_id": run_id,
            "state": snapshot["run"]["state"],
            "event_count": len(snapshot["events"]),
            "timeline": [
                {
                    "event_type": event["event_type"],
                    "state": event["state"],
                    "summary": event["summary"],
                    "output_artifact_ids": event["output_artifact_ids"],
                }
                for event in snapshot["events"]
            ],
        }

    @staticmethod
    def _next_task(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
        succeeded = {
            task["task_id"]
            for task in tasks
            if task["status"] in {TaskStatus.SUCCEEDED.value, TaskStatus.SKIPPED.value}
        }
        for task in tasks:
            if task["status"] in {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}:
                return task
            if task["status"] == TaskStatus.WAITING_HUMAN.value:
                return task
            if task["status"] == TaskStatus.FAILED.value:
                return None
            # Dependencies are encoded in the plan ordering in v0.2.0.
            if task["task_id"] not in succeeded:
                return task
        return None

    @staticmethod
    def _gate_required(goal: ContentGoal, state: RunState) -> bool:
        if goal.autonomy == "auto":
            return False
        if goal.autonomy == "assisted" and state == RunState.WAIT_GATE_1:
            return False
        return state in {RunState.WAIT_GATE_1, RunState.WAIT_GATE_2}

    def _check_budget(self, goal: ContentGoal, spent: dict[str, Any], started: float) -> None:
        reasons = []
        if int(spent.get("llm_calls", 0)) >= goal.budget.max_llm_calls:
            reasons.append("LLM call budget reached")
        if float(spent.get("cost_usd", 0)) >= goal.budget.max_cost_usd:
            reasons.append("cost budget reached")
        if float(spent.get("runtime_seconds", 0)) >= goal.budget.max_runtime_seconds:
            reasons.append("runtime budget reached")
        if reasons:
            self.store.set_paused(goal.run_id, True)
            self.store.update_spent(goal.run_id, spent)
            self._event(
                goal.run_id,
                None,
                "budget.stopped",
                RunState(self._current(goal.run_id)["state"]),
                "; ".join(reasons),
                TaskStatus.WAITING_HUMAN,
            )
            raise BudgetExceeded("; ".join(reasons))

    def _event(
        self,
        run_id: str,
        task_id: str | None,
        event_type: str,
        state: RunState,
        summary: str,
        status: TaskStatus | None = None,
    ) -> None:
        self.store.append_event(
            RunEvent(
                run_id=run_id,
                task_id=task_id,
                event_type=event_type,
                state=state,
                status=status,
                summary=summary,
                input_hash=hashlib.sha256(summary.encode()).hexdigest(),
            )
        )
