"""Deterministic plan skeleton selected from a versioned Content Goal."""

from __future__ import annotations

from c2video.domain.models import ContentGoal, PlanTask, RunPlan, RunState


def select_format(goal: ContentGoal) -> str:
    if goal.preferred_format:
        return goal.preferred_format
    query = goal.query.lower()
    if "thread" in query or "长推" in query or "串" in query:
        return "thread_story"
    if any(token in query for token in ("深挖", "解释", "单条", "一个")):
        return "single_explainer"
    return "news_recap"


def build_plan(goal: ContentGoal) -> RunPlan:
    format_name = select_format(goal)
    definitions = [
        ("discover", RunState.DISCOVER, "content.discover", False),
        ("research", RunState.RESEARCH, "evidence.research", False),
        ("curate", RunState.CURATE, "portfolio.curate", False),
        ("gate_1", RunState.WAIT_GATE_1, None, True),
        ("script", RunState.SCRIPT, "script.compose", False),
        ("script_review", RunState.SCRIPT_REVIEW, "script.review", False),
        ("storyboard", RunState.STORYBOARD, "visual.storyboard", False),
        ("produce", RunState.PRODUCE, "producer.render", False),
        ("quality_review", RunState.QUALITY_REVIEW, "quality.review", False),
        ("repair", RunState.REPAIR, "quality.repair", False),
        ("gate_2", RunState.WAIT_GATE_2, None, True),
    ]
    tasks: list[PlanTask] = []
    previous: str | None = None
    for task_type, state, tool, gate in definitions:
        task = PlanTask(
            task_type=task_type,
            target_state=state,
            tool_name=tool,
            depends_on=[previous] if previous else [],
            exit_conditions=["versioned output validates", "budget remains"],
            max_attempts=2,
            human_gate=gate,
        )
        tasks.append(task)
        previous = task.task_id
    gates = [] if goal.autonomy == "auto" else ["Gate 1", "Gate 2"]
    memory_note = f"；记录 {len(goal.memory_context)} 条偏好上下文（规则模式不消费）" if goal.memory_context else ""
    return RunPlan(
        run_id=goal.run_id,
        format=format_name,
        tasks=tasks,
        decision_summary=(
            f"采用 {format_name}；{goal.autonomy} 自治等级；"
            f"{goal.target_duration_seconds} 秒目标，最多 {goal.budget.max_candidates} 个 Candidate{memory_note}。"
        ),
        human_gates=gates,
    )


def build_compatibility_plan(goal: ContentGoal) -> RunPlan:
    stages = [
        ("fetch", RunState.DISCOVER),
        ("curate", RunState.CURATE),
        ("card", RunState.STORYBOARD),
        ("script", RunState.SCRIPT),
        ("render", RunState.PRODUCE),
    ]
    tasks: list[PlanTask] = []
    previous: str | None = None
    for stage, state in stages:
        task = PlanTask(
            task_type=f"legacy_{stage}",
            target_state=state,
            tool_name=f"legacy.{stage}",
            depends_on=[previous] if previous else [],
            max_attempts=1,
        )
        tasks.append(task)
        previous = task.task_id
    return RunPlan(
        run_id=goal.run_id,
        format="news_recap",
        tasks=tasks,
        decision_summary=(
            f"使用已配置的实时数据源抓取热点，走速览管线做成片；"
            f"{goal.autonomy} 自治；目标 {goal.target_duration_seconds} 秒。"
        ),
        human_gates=[] if goal.autonomy == "auto" else ["Gate 1", "Gate 2"],
    )


def build_live_plan(goal: ContentGoal) -> RunPlan:
    """Live pipelines have executable gates; legacy CLI stage order stays compatible."""
    plan = build_compatibility_plan(goal)
    definitions = [
        ("legacy_fetch", RunState.DISCOVER, "legacy.fetch"),
        ("legacy_curate", RunState.CURATE, "legacy.curate"),
        ("gate_1", RunState.WAIT_GATE_1, None),
        ("legacy_card", RunState.STORYBOARD, "legacy.card"),
        ("legacy_script", RunState.SCRIPT, "legacy.script"),
        ("legacy_render", RunState.PRODUCE, "legacy.render"),
        ("live_quality", RunState.QUALITY_REVIEW, "live.quality"),
        ("gate_2", RunState.WAIT_GATE_2, None),
    ]
    tasks = []
    for kind, state, tool in definitions:
        tasks.append(PlanTask(task_type=kind, target_state=state, tool_name=tool,
                              depends_on=[tasks[-1].task_id] if tasks else [],
                              max_attempts=1, human_gate=tool is None))
    plan.tasks = tasks
    plan.decision_summary += " 实时审核与媒体质检；失败步骤人工确认后重试。"
    return plan
