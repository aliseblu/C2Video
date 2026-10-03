"""Zhihu curation contracts and explainable zero-pick failures; no live calls."""

import json
from pathlib import Path

import pytest

from c2video.config.schema import C2VideoConfig
from c2video.pipeline.curate import MIN_KEEP_SCORE, run_curate, score_candidates
from c2video.pipeline.io import write_candidates
from c2video.pipeline.ledger import Ledger
from c2video.pipeline.prompts import load_prompt
from c2video.source.models import CandidateItem
from c2video.util import search_terms


def candidate(id="a", *, body="先预览文件改动，再确认执行，误操作要支持撤销。"):
    return CandidateItem(
        id=id,
        text="文件整理建议\n" + body,
        raw={
            "source": "zhihu",
            "source_type": "zhihu_answer",
            "title": "文件整理建议",
            "summary": body,
            "content_scope": "api_summary",
            "available_metrics": [],
        },
    )


def decision(id="a", **kwargs):
    return {
        "id": id,
        "score": 8.0,
        "reject": False,
        "reason": "作者提供了具体操作边界。",
        "translation": "先预览再执行，保留撤销能力。",
        **kwargs,
    }


class Model:
    def __init__(self, items):
        self.items, self.calls = items, []
        self.closed = False

    async def complete_structured(self, messages, schema, **kwargs):
        self.calls.append(messages)
        return {"items": self.items}

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("reject", [False, "false", " FALSE "])
async def test_short_zhihu_advice_is_not_rejected_by_old_news_rule(reject):
    model = Model([decision(reject=reject)])
    report = {}
    picks = await score_candidates(
        [candidate()], llm=model, exclude_pick_ids=[], top_n=4, diagnostics=report
    )
    assert [p.id for p in picks] == ["a"]
    assert report["decisions"][0]["decision"] == "passed"
    payload = json.loads(model.calls[0][-1]["content"].split("\n\n", 1)[1])
    assert payload["candidates"][0]["source_type"] == "zhihu_answer"
    assert payload["candidates"][0]["body"] == candidate().raw["summary"]
    assert payload["candidates"][0]["content_scope"] == "api_summary"
    assert "不要求必须是新闻事件" in model.calls[0][-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields,status",
    [
        ({"reject": True}, "model_rejected"),
        ({"reject": "true"}, "model_rejected"),
        ({"score": 6.4}, "below_threshold"),
        ({"score": "NaN"}, "invalid_model_fields"),
        ({"score": True}, "invalid_model_fields"),
        ({"score": 100}, "invalid_model_fields"),
        ({"reject": None}, "invalid_model_fields"),
    ],
)
async def test_no_force_picks_and_invalid_fields_are_explained(fields, status):
    report = {}
    picks = await score_candidates(
        [candidate()],
        llm=Model([decision(**fields)]),
        exclude_pick_ids=[],
        top_n=3,
        diagnostics=report,
    )
    assert picks == [] and report["decisions"][0]["decision"] == status
    assert MIN_KEEP_SCORE == 6.5


@pytest.mark.asyncio
async def test_unknown_duplicate_and_missing_ids_are_not_silently_selected():
    report = {}
    picks = await score_candidates(
        [candidate("a"), candidate("b")],
        llm=Model([decision("wrong"), decision("a"), decision("a")]),
        exclude_pick_ids=[],
        top_n=3,
        diagnostics=report,
    )
    assert picks == [] and report["invalid_ids"] == ["wrong"]
    assert [r["decision"] for r in report["decisions"]] == ["duplicate_model_id", "not_returned"]


@pytest.mark.asyncio
async def test_title_only_cannot_be_accepted_even_if_model_gives_high_score():
    report = {}
    assert (
        await score_candidates(
            [candidate(body="")],
            llm=Model([decision()]),
            exclude_pick_ids=[],
            top_n=1,
            diagnostics=report,
        )
        == []
    )
    assert report["decisions"][0]["decision"] == "missing_body"


@pytest.mark.asyncio
async def test_already_selected_ids_are_not_sent_or_reused():
    model = Model([decision()])
    report = {}
    assert (
        await score_candidates(
            [candidate()], llm=model, exclude_pick_ids=["a"], top_n=3, diagnostics=report
        )
        == []
    )
    assert model.calls == []
    assert report["decisions"][0]["decision"] == "already_picked"


@pytest.mark.asyncio
async def test_zero_picks_saves_scores_and_reasons_without_mutating_ledger(tmp_path):
    cfg = C2VideoConfig(work_dir=str(tmp_path / "work"))
    cfg.llm.provider = "api"
    source = write_candidates(tmp_path / "source.json", [candidate()])
    ledger = Ledger.load(cfg.work_dir)
    ledger.mark_picks(["older"])
    Path(cfg.work_dir).mkdir()
    ledger.save()
    before = ledger.path.read_bytes()
    model = Model([decision(reject=True, reason="这条只有工具广告，没有操作证据。")])
    with pytest.raises(RuntimeError, match="模型淘汰 1 条") as error:
        await run_curate(cfg, date="2026-09-15", input_path=source, llm=model)
    assert "没有操作证据" in str(error.value)
    day = Path(cfg.work_dir) / "2026-09-15"
    report = json.loads((day / "curation-report.json").read_text())
    assert report["input_count"] == 1 and report["passed_count"] == 0
    assert report["decision_counts"] == {"model_rejected": 1}
    assert (day / "scored.json").exists() and "工具广告" in (day / "candidates.md").read_text()
    assert not (day / "picks.json").exists() and ledger.path.read_bytes() == before
    assert "skip-hard-filter" not in str(error.value)


@pytest.mark.asyncio
async def test_success_can_use_fewer_items_than_target_and_preserves_report(tmp_path):
    cfg = C2VideoConfig(work_dir=str(tmp_path / "work"))
    cfg.llm.provider = "api"
    source = write_candidates(tmp_path / "source.json", [candidate("a"), candidate("b")])
    result = await run_curate(
        cfg, input_path=source, llm=Model([decision("a"), decision("b", reject=True)]), top_n=4
    )
    assert [p.id for p in result["picks"]] == ["a"]
    report = json.loads(result["report"].read_text())
    assert report["selected_ids"] == ["a"]
    assert report["decision_counts"] == {"passed": 1, "model_rejected": 1}
    assert Ledger.load(cfg.work_dir).is_pick("a") and not Ledger.load(cfg.work_dir).is_pick("b")


@pytest.mark.asyncio
async def test_all_excluded_message_does_not_suggest_relaxing_filters(tmp_path):
    cfg = C2VideoConfig(work_dir=str(tmp_path / "work"))
    cfg.llm.provider = "api"
    Path(cfg.work_dir).mkdir()
    ledger = Ledger.load(cfg.work_dir)
    ledger.mark_picks(["a"])
    ledger.save()
    source = write_candidates(tmp_path / "source.json", [candidate()])
    model = Model([])
    with pytest.raises(RuntimeError, match="均已入选过"):
        await run_curate(cfg, input_path=source, llm=model)
    assert not model.calls


def test_default_goal_extracts_topics_not_video_instructions():
    terms = search_terms(
        "整理9月份知乎上的 AI 与科技话题，保留原文出处，制作中文口播视频。",
        ["AI", "artificial intelligence", "machine learning", "LLM"],
    )
    assert terms == ["AI", "科技", "artificial intelligence", "machine learning", "LLM"]
    assert "文件整理工具" in search_terms("整理知乎上的文件整理工具话题，保留原文出处")
    assert "整理" in search_terms("搜索关于“整理”的工具")


def test_root_prompt_matches_installed_prompt_and_allows_substantive_opinions():
    prompt = load_prompt("curation-prompt.md")
    assert prompt == (Path(__file__).parents[1] / "c2video/prompts/curation-prompt.md").read_text()
    assert "只留资讯" not in prompt and "观点都可以入选" in prompt
    assert "不为了凑 top_n 强行选材" in prompt
