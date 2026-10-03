"""Curation stage: LLM scoring → candidates.md → picks.json (Gate 1)."""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from c2video.config.schema import C2VideoConfig
from c2video.llm.api_impl import LLMHTTPError
from c2video.llm.base import LLMProvider
from c2video.llm.client import create_llm_provider
from c2video.pipeline.io import load_candidates, write_json, write_picks
from c2video.pipeline.ledger import Ledger
from c2video.pipeline.models import Pick
from c2video.pipeline.prompts import load_prompt
from c2video.pipeline.workdir import resolve_run_dir
from c2video.preferences import preference_messages
from c2video.source.models import CandidateItem
from c2video.util import format_count, parse_json_payload

CURATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                    "translation": {"type": "string"},
                    "reject": {"type": "boolean"},
                },
                "required": ["id", "score", "reason", "translation", "reject"],
            },
        }
    },
    "required": ["items"],
}

MIN_KEEP_SCORE = 6.5

_FLUFF_RE = re.compile(
    r"生活更美好|改变世界|让生活|美好生活|正能量|治愈|爱了|太酷了|冲就完了|"
    r"感谢AI|感谢特斯拉|makes life better|changed my life",
    re.I,
)
_NEWS_RE = re.compile(
    r"发布|推出|开源|论文|融资|收购|更新|评测|宣布|下线|涨价|泄露|泄漏|赢得|击败|"
    r"突破|禁用|封禁|召回|裁员|上市|爆料|模型|权重|基准|benchmark|launch|release|"
    r"paper|acquire|open.?source|update|ban|recall",
    re.I,
)


def is_newsworthy(text: str, translation: str = "", reason: str = "") -> bool:
    """Drop fluff that is not a piece of news someone else needs to hear."""
    blob = f"{text}\n{translation}\n{reason}"
    stripped = (text or "").strip()
    if _FLUFF_RE.search(blob) and not _NEWS_RE.search(blob):
        return False
    if len(stripped) < 36 and not _NEWS_RE.search(blob):
        return False
    return True


def _candidate_payload(c: CandidateItem) -> dict[str, Any]:
    return {
        "id": c.id,
        "text": c.text,
        "body": _candidate_body(c),
        "title": str(c.raw.get("title") or ""),
        "source": _public_source(c),
        "source_type": str(c.raw.get("source_type") or ""),
        "content_scope": str(c.raw.get("content_scope") or "provided_text"),
        "available_metrics": c.raw.get("available_metrics", []),
        "author_name": c.author_name,
        "author_username": c.author_username,
        "likes": c.likes,
        "shares": c.shares,
        "replies": c.replies,
        "views": c.views,
        "url": c.url,
        "created_at": c.created_at,
        "has_media": bool(c.media_urls),
    }


def _parse_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        items = raw.get("items") or raw.get("candidates") or raw.get("picks")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    return []


def _public_source(candidate: CandidateItem) -> str:
    return str(candidate.raw.get("source") or "unknown").strip().lower()


def _local_summary(candidate: CandidateItem) -> str:
    source = _public_source(candidate)
    if source == "zhihu":
        return str(candidate.raw.get("title") or candidate.text).strip()
    if source == "github":
        return "AI 开源项目受到关注"
    if source == "hacker_news":
        return "AI 社区出现新讨论"
    if source == "rss":
        return "AI 官方博客发布新动态"
    return candidate.text.strip()


def score_candidates_locally(
    candidates: list[CandidateItem],
    *,
    exclude_pick_ids: list[str],
    top_n: int,
    theme: str | None = None,
) -> list[Pick]:
    """Rank public items with transparent rules and no model or account."""
    excluded = set(exclude_pick_ids)
    theme_terms = [term.casefold() for term in re.split(r"[\s,，]+", theme or "") if term]
    source_bonus = {"github": 0.8, "hacker_news": 0.6, "rss": 0.4, "zhihu": 0.4}
    scored: list[Pick] = []
    for candidate in candidates:
        if candidate.id in excluded:
            continue
        source = _public_source(candidate)
        public_item = source in source_bonus
        if not public_item and not is_newsworthy(candidate.text):
            continue
        engagement = max(candidate.engagement_score(), 0)
        engagement_bonus = min(1.5, math.log10(engagement + 1) * 0.45)
        theme_bonus = 0.0
        folded = candidate.text.casefold()
        if theme_terms and any(term in folded for term in theme_terms):
            theme_bonus = 0.5
        score = min(10.0, 6.8 + source_bonus.get(source, 0.2) + engagement_bonus + theme_bonus)
        reason = (
            f"本地规则：{source or '来源'} 数据，互动权重 {engagement}，"
            "按来源可信度、热度和主题相关性排序"
        )
        scored.append(
            Pick(
                **candidate.model_dump(),
                translation=_local_summary(candidate),
                score=round(score, 2),
                reason=reason,
            )
        )
    scored.sort(key=lambda pick: (pick.score, pick.engagement_score()), reverse=True)
    return scored[: max(top_n * 3, top_n)]


def _candidate_body(candidate: CandidateItem) -> str:
    body = str(candidate.raw.get("summary") or candidate.raw.get("description") or "").strip()
    if not body:
        body = candidate.text.strip()
        title = str(candidate.raw.get("title") or "").strip()
        if title and body.startswith(title):
            body = body[len(title) :].strip()
    return body


def _model_reject(value: Any) -> bool:
    # Some compatible models return "false"; bool("false") would reject it.
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError("reject must be a boolean")


async def score_candidates(
    candidates: list[CandidateItem],
    *,
    llm: LLMProvider,
    exclude_pick_ids: list[str],
    top_n: int,
    theme: str | None = None,
    diagnostics: dict[str, Any] | None = None,
    preferences: list[dict] | None = None,
) -> list[Pick]:
    report = diagnostics if diagnostics is not None else {}
    excluded = set(exclude_pick_ids)
    by_id = {c.id: c for c in candidates}
    rows = {
        c.id: {
            "id": c.id,
            "title": str(c.raw.get("title") or c.text.split("\n")[0])[:120],
            "source": _public_source(c),
            "score": None,
            "model_reason": "",
            "decision": "already_picked" if c.id in excluded else "not_returned",
        }
        for c in candidates
    }
    report["decisions"] = list(rows.values())
    report["returned_items"] = 0
    report["invalid_ids"] = []
    eligible = [c for c in candidates if c.id not in excluded]
    if not eligible:
        return []
    system = load_prompt("curation-prompt.md")
    theme_line = f"用户指定主题：{theme.strip()}\n" if theme and theme.strip() else ""
    user = (
        theme_line + "为中文短视频选择可忠实概括的内容。知乎回答、经验复盘和有依据的观点都可入选，"
        "不要求必须是新闻事件，不因是中文而淘汰。\n"
        f"最多选 {top_n} 条，但对下面每个候选 ID 都要返回评分和明确理由。"
        "只读正文和摘要，不补写事实，不为凑数量放行不合格内容。\n\n"
        f"{_json_dumps({'candidates': [_candidate_payload(c) for c in eligible]})}"
    )
    if len(user) > 100_000:
        raise ValueError("选材输入过长，请减少候选数量或正文长度。")
    messages = [
        {"role": "system", "content": system},
        *preference_messages(preferences, "selection"),
        {"role": "user", "content": user},
    ]
    try:
        parsed = await llm.complete_structured(messages, CURATION_SCHEMA)
    except LLMHTTPError:
        raise
    except ValueError:
        text = await llm.complete(messages)
        parsed = parse_json_payload(text)

    items = _parse_items(parsed)
    report["returned_items"] = len(items)
    scored: dict[str, Pick] = {}
    seen: set[str] = set()
    for item in items:
        tid = str(item.get("id") or "").strip()
        if tid not in by_id:
            report["invalid_ids"].append(tid[:120])
            continue
        row = rows[tid]
        if tid in excluded:
            continue
        if tid in seen:
            row["decision"] = "duplicate_model_id"
            scored.pop(tid, None)
            continue
        seen.add(tid)
        row["model_reason"] = str(item.get("reason") or "").strip()[:1200]
        try:
            reject = _model_reject(item.get("reject"))
            raw_score = item.get("score")
            if isinstance(raw_score, bool):
                raise ValueError("boolean score")
            score = float(raw_score)
            if not math.isfinite(score) or not 0 <= score <= 10:
                raise ValueError("score outside 0–10")
        except (TypeError, ValueError, OverflowError):
            row["decision"] = "invalid_model_fields"
            continue
        row.update(score=score, model_reject=reject)
        base = by_id[tid]
        pick = Pick(
            **base.model_dump(),
            translation=str(item.get("translation") or "").strip(),
            score=score,
            reason=row["model_reason"],
        )
        if reject:
            row["decision"] = "model_rejected"
        elif score < MIN_KEEP_SCORE:
            row["decision"] = "below_threshold"
        elif _public_source(base) == "zhihu" and not _candidate_body(base):
            row["decision"] = "missing_body"
        elif _public_source(base) != "zhihu" and not is_newsworthy(
            base.text, pick.translation, pick.reason
        ):
            row["decision"] = "local_quality_filter"
        else:
            row["decision"] = "passed"
            scored[tid] = pick
    return sorted(scored.values(), key=lambda p: p.score, reverse=True)


def render_candidates_md(scored: list[Pick], *, date: str, kept_ids: set[str]) -> str:
    lines = [
        f"# Candidates {date}",
        "",
        "勾选 Pick：把要做的条目编号留给 Gate 1，或使用 `--auto` 按分数取前 N。",
        "",
    ]
    if not scored:
        lines.append("_No candidates scored above threshold._")
        return "\n".join(lines) + "\n"
    for i, p in enumerate(scored, start=1):
        mark = "✓ Pick" if p.id in kept_ids else ""
        lines.extend(
            [
                f"## {i}. {p.score:.1f} @{p.author_username or 'unknown'} {mark}",
                "",
                f"- 作者: {p.author_name} (@{p.author_username})",
                f"- 互动: {format_count(p.likes)} 赞同 / {format_count(p.shares)} 分享 / "
                f"{format_count(p.replies)} replies / {format_count(p.views)} views",
                f"- 链接: {p.url}",
                f"- 理由: {p.reason}",
                "",
                "原文:",
                "",
                f"> {p.text.replace(chr(10), chr(10) + '> ')}",
                "",
                "翻译:",
                "",
                f"> {p.translation}",
                "",
            ]
        )
    return "\n".join(lines)


def select_picks(scored: list[Pick], *, top_n: int, indices: list[int] | None) -> list[Pick]:
    if indices:
        out: list[Pick] = []
        for i in indices:
            if 1 <= i <= len(scored):
                out.append(scored[i - 1])
        return out
    return scored[:top_n]


_DECISION_LABELS = {
    "passed": "达到选材标准",
    "already_picked": "历史已入选",
    "model_rejected": "模型淘汰",
    "below_threshold": "评分不足",
    "not_returned": "模型未返回该条评分",
    "invalid_model_fields": "模型评分格式无效",
    "duplicate_model_id": "模型重复返回同一 ID",
    "missing_body": "只有标题、缺少正文",
    "local_quality_filter": "本地内容规则过滤",
    "local_not_selected": "本地规则未选入",
}


def _write_curation_report(
    run_dir: Path, report: dict[str, Any], scored: list[Pick], picks: list[Pick]
) -> tuple[Path, Path]:
    report["passed_count"] = len(scored)
    report["selected_ids"] = [p.id for p in picks]
    report["decision_counts"] = dict(Counter(r["decision"] for r in report.get("decisions", [])))
    path = write_json(run_dir / "curation-report.json", report)
    md = render_candidates_md(scored, date=run_dir.name, kept_ids={p.id for p in picks})
    md += "\n## 逐条筛选记录\n\n"
    for row in report.get("decisions", []):
        label = _DECISION_LABELS.get(row["decision"], row["decision"])
        reason = row.get("model_reason") or "无模型理由"
        md += f"- {row['id']} | {label} | 评分 {row.get('score')} | {reason}\n"
    md_path = run_dir / "candidates.md"
    md_path.write_text(md, encoding="utf-8")
    write_json(run_dir / "scored.json", {"items": [p.model_dump() for p in scored]})
    return path, md_path


def _empty_curation_message(report: dict[str, Any], path: Path, *, invalid_selection: bool) -> str:
    total = report["input_count"]
    counts = report["decision_counts"]
    if not total:
        detail = "没有候选素材，请先抓取或导入含正文的素材。"
    elif counts.get("already_picked", 0) == total:
        detail = "候选素材均已入选过，请搜索其他内容；没有清除历史去重记录。"
    elif invalid_selection:
        detail = "已有合格候选，但指定的编号未选中任何条目，请检查选材编号。"
    else:
        parts = [f"{_DECISION_LABELS.get(key, key)} {value} 条" for key, value in counts.items()]
        detail = "、".join(parts) + "。"
        if counts.get("model_rejected") or counts.get("below_threshold"):
            reasons = [
                r["model_reason"]
                for r in report["decisions"]
                if r["decision"] in {"model_rejected", "below_threshold"} and r.get("model_reason")
            ]
            if reasons:
                detail += "示例原因：" + "；".join(r[:100] for r in reasons[:2]) + "。"
        detail += "请按逐条原因调整主题或导入具体正文，不会自动降低标准。"
    return f"选材后无可用内容（输入 {total} 条）。{detail}筛选报告：{path}"


async def run_curate(
    cfg: C2VideoConfig,
    *,
    date: str | None = None,
    input_path: Path | None = None,
    output_path: Path | None = None,
    auto: bool = True,
    indices: list[int] | None = None,
    llm: LLMProvider | None = None,
    top_n: int | None = None,
    theme: str | None = None,
    preferences: list[dict] | None = None,
) -> dict[str, Any]:
    run_dir = resolve_run_dir(cfg.work_dir, date)
    src = input_path or (run_dir / "candidates.json")
    if not src.exists():
        raise FileNotFoundError(f"Candidates file not found: {src}. Run `c2video fetch` first.")

    _meta, candidates = load_candidates(src)
    ledger = Ledger.load(cfg.work_dir)
    exclude = list(ledger.picks.keys())

    keep = int(top_n or cfg.curation.top_n)
    report: dict[str, Any] = {
        "mode": cfg.llm.provider,
        "input_count": len(candidates),
        "min_keep_score": MIN_KEEP_SCORE,
        "theme": theme or "",
    }
    if cfg.llm.provider.strip().lower() == "local":
        scored = score_candidates_locally(
            candidates,
            exclude_pick_ids=exclude,
            top_n=keep,
            theme=theme,
        )
        kept = {p.id for p in scored}
        report["decisions"] = [
            {
                "id": c.id,
                "title": str(c.raw.get("title") or "")[:120],
                "decision": "already_picked"
                if c.id in exclude
                else ("passed" if c.id in kept else "local_not_selected"),
            }
            for c in candidates
        ]
    else:
        owns_llm = llm is None
        llm = llm or create_llm_provider(cfg.llm.model_copy(update={"telemetry_purpose": "curate"}))
        try:
            scored = await score_candidates(
                candidates,
                llm=llm,
                exclude_pick_ids=exclude,
                top_n=keep,
                theme=theme,
                diagnostics=report,
                preferences=preferences,
            )
        except Exception as exc:
            report["error_type"] = type(exc).__name__
            _write_curation_report(run_dir, report, [], [])
            raise
        finally:
            if owns_llm:
                await llm.close()

    picks = select_picks(scored, top_n=keep, indices=indices)
    report_path, md_path = _write_curation_report(run_dir, report, scored, picks)
    if not picks:
        raise RuntimeError(
            _empty_curation_message(report, report_path, invalid_selection=bool(scored))
        )
    day = run_dir.name

    dest = output_path or (run_dir / "picks.json")
    write_picks(
        dest,
        picks,
        meta={
            "date": day,
            "auto": auto,
            "scored": len(scored),
            "top_n": cfg.curation.top_n,
        },
    )
    if picks:
        ledger.mark_picks((p.id for p in picks), extra={"date": day})
        ledger.save()

    return {
        "input": src,
        "output": dest,
        "markdown": md_path,
        "scored": scored,
        "picks": picks,
        "report": report_path,
    }


def _json_dumps(payload: Any) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)
