"""Single application service shared by CLI, API, Worker, and Studio."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from c2video.agent.locking import run_lock
from c2video.agent.planner import build_live_plan, build_plan
from c2video.agent.runtime import AgentRuntime
from c2video.config.schema import C2VideoConfig
from c2video.domain.models import (
    ContentGoal,
    EditorialDecision,
    RunEvent,
    RunState,
    UserFeedback,
    new_id,
)
from c2video.io_atomic import atomic_write_text
from c2video.memory import (
    MemoryProcessingError,
    MemoryProcessingResult,
    extract_memories,
    memory_availability,
    memory_failure,
)
from c2video.security import redact_text, untrusted_content_risks
from c2video.source.status import source_status
from c2video.source.zhihu import parse_materials, read_materials
from c2video.storage.jobs import RunQueue
from c2video.storage.run_store import RunStore
from c2video.tools.content import register_content_tools
from c2video.tools.legacy_pipeline import register_legacy_tools
from c2video.tools.live_quality import LiveQualityTool
from c2video.tools.registry import ToolRegistry


class ApplicationService:
    def __init__(
        self,
        *,
        work_dir: str = "work",
        db_path: str | None = None,
        config: C2VideoConfig | None = None,
    ) -> None:
        self.work_dir = str(Path(work_dir).resolve())
        self.db_path = str(Path(db_path or Path(work_dir) / "c2video-agent.db").resolve())
        self.config = config.model_copy(deep=True) if config is not None else None
        if self.config is not None:
            self.config.llm.telemetry_db_path = self.db_path
        self.store = RunStore(self.db_path)
        registry = ToolRegistry()
        register_content_tools(registry, self.store)
        if self.config is not None:
            register_legacy_tools(registry, self.config, self.store)
            registry.register(LiveQualityTool(self.store))
        self.runtime = AgentRuntime(self.store, registry, work_dir=self.work_dir)

    def live_ready(self) -> bool:
        return bool(source_status(self.config)["configured"])

    def resolve_mode(self, requested: str | None = None) -> str:
        if requested == "demo":
            return "demo"
        if requested == "live" and self.config is None:
            raise ValueError("Live mode needs a loaded c2video.toml")
        # A configured source must not silently become an unrelated fixture demo.
        return "live" if self.config is not None else "demo"

    def create_run(
        self,
        *,
        query: str,
        autonomy: str = "assisted",
        target_duration_seconds: int = 60,
        preferred_format: str | None = None,
        mode: str | None = None,
        materials: list[dict[str, Any]] | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query or len(query) > 4000 or not 20 <= target_duration_seconds <= 600:
            raise ValueError("主题需为 1–4000 字符，目标时长需为 20–600 秒。")
        if mode not in {None, "live", "demo"}:
            raise ValueError("mode must be live or demo")
        run_id = new_id("run")
        approved_memories = self.store.list_memories(status="approved")
        resolved_mode = self.resolve_mode(mode)
        import_payload = materials
        if materials is not None and (resolved_mode != "live" or self.config is None
                                      or self.config.source.provider != "zhihu"):
            raise ValueError("知乎素材导入需要知乎数据源和生产模式。")
        if resolved_mode == "live" and self.config is not None:
            if self.config.source.provider == "zhihu":
                if import_payload is None and self.config.source.zhihu_mode == "import":
                    import_payload = read_materials(self.config.source.zhihu_import_file)
                if import_payload is not None:
                    parse_materials(import_payload)
                elif not self.live_ready():
                    raise ValueError(source_status(self.config)["detail"])
            elif not self.live_ready():
                raise ValueError(source_status(self.config)["detail"])
        if import_payload is not None:
            input_path = Path(self.work_dir) / "agent_runs" / run_id / "source.zhihu.json"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(input_path, json.dumps(import_payload, ensure_ascii=False))
        goal = ContentGoal(
            run_id=run_id,
            query=query,
            autonomy=autonomy,
            target_duration_seconds=target_duration_seconds,
            preferred_format=preferred_format,
            memory_context=[item["content"] for item in approved_memories[:10]],
            memory_preferences=[{k: item[k] for k in ("memory_id", "content", "scope")}
                                for item in approved_memories if item.get("ai_processed") and item.get("scope") in
                                {"selection", "script", "visual", "quality"}][:10],
        )
        if resolved_mode == "live":
            goal.budget.max_runtime_seconds = max(goal.budget.max_runtime_seconds, 1800)
            plan = build_live_plan(goal)
        else:
            plan = build_plan(goal)
        self.runtime.create(goal, plan)
        snapshot = self.get_run(run_id) or {}
        if snapshot.get("run"):
            snapshot["run"]["mode"] = resolved_mode
        return snapshot

    def feedback(
        self,
        run_id: str,
        *,
        category: str,
        comment: str,
        rating: int | None = None,
        target_id: str | None = None,
    ) -> dict[str, Any]:
        """Synchronous CLI entry; HTTP callers use feedback_async."""
        return asyncio.run(self.feedback_async(
            run_id, category=category, comment=comment, rating=rating, target_id=target_id,
        ))

    async def feedback_async(
        self,
        run_id: str,
        *,
        category: str,
        comment: str,
        rating: int | None = None,
        target_id: str | None = None,
    ) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise KeyError(f"Run not found: {run_id}")
        feedback = UserFeedback(
            run_id=run_id, category=category, comment=comment.strip(),
            rating=rating, target_id=target_id,
        )
        if not feedback.comment:
            raise ValueError("Feedback comment cannot be empty")
        self.store.add_feedback(feedback)
        self.store.append_event(RunEvent(
            run_id=run_id,
            event_type="feedback.recorded",
            state=RunState(run["state"]),
            summary=f"Recorded {category} feedback",
            payload={"feedback_id": feedback.feedback_id},
        ))
        processed = await self._remember_feedback(feedback)
        # Read the current state again: rendering may finish while the model runs.
        current = self.store.get_run(run_id) or run
        self.store.append_event(RunEvent(
            run_id=run_id,
            event_type=f"memory.processing.{processed.status}",
            state=RunState(current["state"]),
            summary=processed.message,
            payload={"feedback_id": feedback.feedback_id, **processed.model_dump()},
        ))
        return {
            "feedback_id": feedback.feedback_id,
            "memory_id": next(iter(processed.memory_ids), None),
            "memory_ids": processed.memory_ids,
            "memory_processing": processed.model_dump(),
        }

    async def _remember_feedback(self, feedback: UserFeedback) -> MemoryProcessingResult:
        availability = memory_availability(self.config)
        if not availability["ready"]:
            return MemoryProcessingResult(
                status="unavailable" if availability["enabled"] else "disabled",
                message=f"反馈已保存。{availability['detail']}；未写入新记忆。",
            )
        safe_comment = redact_text(feedback.comment)
        if (safe_comment != feedback.comment or untrusted_content_risks(safe_comment)
                or "[REDACTED]" in safe_comment):
            return MemoryProcessingResult(
                status="skipped", message="反馈已保存；包含敏感信息或指令风险，未写入记忆。",
            )
        try:
            existing = self.store.list_memories()
        except Exception:
            return memory_failure("memory_read_failed")
        try:
            candidates = await extract_memories(self.config, feedback, existing)
        except MemoryProcessingError as exc:
            return memory_failure(
                exc.code, http_status=exc.http_status, validation_issues=exc.validation_issues,
            )
        except Exception:
            # Unexpected local processing errors remain distinct from provider
            # failures; never leak exception text or copy raw feedback as memory.
            return memory_failure("processing_failed")
        try:
            memory_ids = self.store.add_ai_memories(candidates)
        except Exception:
            return memory_failure("memory_write_failed")
        return MemoryProcessingResult(
            status="stored" if memory_ids else "skipped",
            message=(f"反馈已保存，AI 已整理并自动记住 {len(memory_ids)} 条长期偏好。"
                     if memory_ids else "反馈已保存；没有新的、足够明确的长期偏好可写入。"),
            memory_ids=memory_ids,
        )

    async def execute(self, run_id: str) -> dict[str, Any]:
        return await self.runtime.run(run_id)

    def execute_sync(self, run_id: str) -> dict[str, Any]:
        return asyncio.run(self.execute(run_id))

    def start_worker(self, run_id: str) -> dict[str, Any]:
        """Persist one execution request. The supervisor owns process creation."""
        return RunQueue(self.store).enqueue(run_id)

    def list_runs(self) -> list[dict[str, Any]]:
        return self.store.list_runs()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        snapshot = self.store.snapshot(run_id)
        if not snapshot:
            return None
        root = Path(self.work_dir) / "agent_runs" / run_id
        documents = {}
        unavailable = []
        for name in (
            "curation.json",
            "claim_map.json",
            "script.draft.json",
            "script.final.json",
            "script.review.json",
            "storyboard.json",
            "publish_kit/qc.before.json",
            "publish_kit/qc.after.json",
            "publish_kit/repair.json",
            "publish_kit/render_manifest.json",
        ):
            path = root / name
            try:
                if path.exists() and path.stat().st_size < 2_000_000:
                    documents[name] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                unavailable.append(name)
        publish = root / "publish_kit"
        snapshot["documents"] = documents
        snapshot["documents_unavailable"] = unavailable
        snapshot["media"] = {
            "video": f"/api/runs/{run_id}/media/video" if (publish / "video.mp4").exists() else None,
            "cover": f"/api/runs/{run_id}/media/cover" if (publish / "cover.png").exists() else None,
            "publish": f"/api/runs/{run_id}/media/publish" if (publish / "publish.md").exists() else None,
        }
        live = any(
            str((task.get("tool_name") or "")).startswith("legacy.")
            for task in (snapshot.get("plan") or {}).get("tasks") or []
        )
        snapshot["run"]["mode"] = "live" if live else "demo"
        return snapshot

    def action(self, run_id: str, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        if action == "fork":
            return self.fork(run_id)
        if action == "pause":
            self.runtime.pause(run_id)
        elif action == "resume":
            self.runtime.resume(run_id)
        elif action == "cancel":
            self.runtime.cancel(run_id, summary=payload.get("summary", "Canceled in Studio"))
        elif action == "retry":
            self.runtime.retry(run_id, task_id=payload.get("task_id"))
        elif action in {"approve_gate", "reject_gate"}:
            self.runtime.approve_gate(
                run_id,
                approved=action == "approve_gate",
                summary=payload.get("summary", ""),
            )
        elif action in {"approve_candidate", "reject_candidate", "reorder", "request_research"}:
            with run_lock(self.db_path, run_id), self.store.atomic():
                self._record_editorial_action(run_id, action, payload)
        elif action in {"lock_segment", "rewrite_segment"}:
            self._edit_script(run_id, action, payload)
        else:
            raise ValueError(f"Unsupported action: {action}")
        return self.get_run(run_id) or {}

    def fork(self, run_id: str) -> dict[str, Any]:
        snapshot = self.get_run(run_id)
        if not snapshot:
            raise KeyError(f"Run not found: {run_id}")
        goal = snapshot["goal"]
        parent_live = any(
            str((task.get("tool_name") or "")).startswith("legacy.")
            for task in (snapshot.get("plan") or {}).get("tasks") or []
        )
        forked = self.create_run(
            query=goal["query"],
            autonomy=goal["autonomy"],
            target_duration_seconds=goal["target_duration_seconds"],
            preferred_format=goal.get("preferred_format"),
            mode="live" if parent_live else "demo",
            materials=json.loads((Path(self.work_dir) / "agent_runs" / run_id / "source.zhihu.json").read_text())
                if (Path(self.work_dir) / "agent_runs" / run_id / "source.zhihu.json").is_file() else None,
        )
        child_id = forked["run"]["run_id"]
        self.store.set_parent(child_id, run_id)
        self.store.append_event(
            RunEvent(
                run_id=child_id,
                event_type="run.forked",
                state=RunState.PLAN,
                summary=f"Forked from {run_id}",
                payload={"parent_run_id": run_id},
            )
        )
        return self.get_run(child_id) or {}

    def _record_editorial_action(self, run_id: str, action: str, payload: dict[str, Any]) -> None:
        snapshot = self.get_run(run_id)
        if not snapshot:
            raise KeyError("Run not found")
        if snapshot["run"]["mode"] == "live" and not (
            snapshot["run"]["state"] == "WAIT_GATE_1" and any(
                t["status"] == "waiting_human" for t in snapshot["tasks"]
            )
        ):
            raise ValueError("实时选题仅能在选题审核阶段修改。")
        decisions = [
            EditorialDecision.model_validate(item)
            for item in self.store.payloads("decisions", run_id)
        ]
        candidate_id = payload.get("candidate_id")
        if action in {"approve_candidate", "reject_candidate"}:
            decision = next(
                (item for item in decisions if item.candidate_id == candidate_id),
                None,
            )
            if not decision:
                raise ValueError("Candidate decision not found")
            decision.selected = action == "approve_candidate"
            decision.decision_summary = str(
                payload.get("summary")
                or ("用户在 Gate 1 批准" if decision.selected else "用户在 Gate 1 拒绝")
            )
            self.store.add_decision(decision)
        elif action == "reorder":
            order = [str(item) for item in payload.get("candidate_ids", [])]
            if not order:
                raise ValueError("candidate_ids is required for reorder")
            for decision in decisions:
                if decision.candidate_id in order:
                    decision.rank = order.index(decision.candidate_id) + 1
                    self.store.add_decision(decision)
        run = self.store.get_run(run_id)
        state = RunState(run["state"]) if run else RunState.CURATE
        self.store.append_event(
            RunEvent(
                run_id=run_id,
                event_type=f"curation.{action}",
                state=state,
                summary=payload.get("summary") or action.replace("_", " "),
                payload=payload,
            )
        )

    def _edit_script(self, run_id: str, action: str, payload: dict[str, Any]) -> None:
        snapshot = self.get_run(run_id)
        if not snapshot:
            raise KeyError("Run not found")
        if snapshot["run"]["mode"] == "live":
            raise ValueError("实时口播暂不支持就地编辑，请调整素材或偏好后新建任务。")
        path = Path(self.work_dir) / "agent_runs" / run_id / "script.final.json"
        if not path.exists():
            raise ValueError("Run has no editable Script")
        document = json.loads(path.read_text(encoding="utf-8"))
        segment_id = payload.get("segment_id")
        segment = next((item for item in document.get("segments", []) if item["segment_id"] == segment_id), None)
        if not segment:
            raise ValueError("Segment not found")
        if action == "lock_segment":
            segment["locked"] = bool(payload.get("locked", True))
        elif segment.get("locked"):
            raise ValueError("Locked Segment cannot be rewritten")
        else:
            segment["narration"] = str(payload.get("narration") or segment["narration"])
            segment["revision"] = int(segment.get("revision", 1)) + 1
        atomic_write_text(path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        run = self.store.get_run(run_id)
        self.store.append_event(
            RunEvent(
                run_id=run_id,
                event_type=f"script.{action}",
                state=RunState(run["state"]) if run else RunState.SCRIPT_REVIEW,
                summary=f"{action}: {segment_id}",
                payload={"segment_id": segment_id, "revision": segment.get("revision")},
            )
        )
