"""Short-video editorial regression cases, using self-authored fixtures only."""

import json
from pathlib import Path

import pytest

from c2video.config.schema import C2VideoConfig
from c2video.pipeline.io import load_script, write_picks
from c2video.pipeline.keypoints import summarize_keypoints
from c2video.pipeline.models import Pick
from c2video.pipeline.prompts import load_prompt
from c2video.pipeline.script import _coerce_script, build_local_script, run_script

BODY = (
    "大家好，今天聊聊我们做项目的一段经历。"
    "过去我们总是花时间讨论选哪一种模型。"
    "测试中，工具报错后没有重试，导致任务中断。"
    "结论：Agent 落地的关键不是模型越大越好，而是把工具失败后的恢复流程做好。"
    "但这个结论只适用于本次内部测试，不能推广到所有任务。"
)


def pick(body=BODY, **extra):
    return Pick(
        id="sample",
        text="如何做好 Agent？\n" + body,
        author_name="测试作者",
        url="https://www.zhihu.com/question/100/answer/200",
        raw={
            "source": "zhihu",
            "title": "如何做好 Agent？",
            "summary": body,
            "content_scope": "provided_text",
        },
        **extra,
    )


def test_conclusion_at_end_beats_greeting_and_background():
    script = build_local_script([pick()], target_duration_seconds=30)
    narration = script.segments[0].narration
    assert narration.startswith("原文提到，Agent 落地的关键不是")
    assert "而是把工具失败后的恢复流程做好" in narration
    assert "不能推广到所有任务" in narration
    assert "工具报错后没有重试" in narration
    assert all(s not in narration for s in ["大家好", "过去我们", "第1条", "知乎话题", "…"])
    assert script.opener_text() == "" and script.outro == ""
    assert script.spoken_texts() == [narration]
    assert "抽取式" in script.description


def test_short_budget_preserves_complete_negation_and_attached_caveat():
    result = summarize_keypoints(BODY, budget=20)
    assert "不是模型越大越好，而是" in result
    assert "只适用于本次内部测试，不能推广到所有任务" in result
    assert result.endswith("。") and "…" not in result


def test_very_short_body_is_not_padded_to_fill_duration():
    script = build_local_script([pick("模型目前仅支持中文输入。")], target_duration_seconds=120)
    assert script.segments[0].narration == "原文提到，模型目前仅支持中文输入。"


def test_use_full_text_when_raw_summary_is_unavailable():
    material = pick()
    material.raw.pop("summary")
    assert "恢复流程做好" in build_local_script([material]).segments[0].narration


@pytest.mark.parametrize("body", ["", "大家好！欢迎来到我的专栏！", "这个模型真的更好吗？"])
def test_insufficient_material_does_not_turn_into_an_invented_answer(body):
    with pytest.raises(ValueError, match="正文|摘要|标题"):
        build_local_script([pick(body)])


def test_only_repeated_title_is_not_body():
    with pytest.raises(ValueError, match="仅提供了标题"):
        build_local_script(
            [
                Pick(
                    id="x",
                    text="如何做好 Agent？",
                    raw={"source": "zhihu", "title": "如何做好 Agent？"},
                )
            ]
        )


def test_numbers_units_and_source_stance_survive():
    body = "结论：我认为延迟可能降到 1.5 秒，但尚未进行生产环境验证。"
    result = build_local_script([pick(body)]).segments[0].narration
    assert result == "原文提到，延迟可能降到 1.5 秒，但尚未进行生产环境验证。"


def test_quoted_negation_is_not_cut_at_sentence_boundary():
    result = summarize_keypoints("结论：作者强调“不要直接上线。必须先做评测”。", budget=10)
    assert "“不要直接上线。必须先做评测”" in result


def test_near_duplicates_are_not_repeated():
    result = summarize_keypoints("结论：应先补齐失败重试。结论：应先补齐失败重试。", budget=200)
    assert result.count("应先补齐失败重试") == 1


@pytest.mark.asyncio
async def test_local_stage_persists_actual_spoken_keypoints(tmp_path):
    cfg = C2VideoConfig(work_dir=str(tmp_path / "work"))
    source = write_picks(tmp_path / "picks.json", [pick()])
    output = tmp_path / "result.json"
    result = await run_script(
        cfg, input_path=source, output_path=output, target_duration_seconds=30
    )
    saved = load_script(output)
    assert saved == result["script"]
    assert saved.opener_text() == ""
    assert "恢复流程做好" in output.with_suffix(".md").read_text()
    assert saved.segments[0].subtitle_lines


@pytest.mark.asyncio
async def test_model_stage_receives_all_available_content_and_source_scope(tmp_path, monkeypatch):
    cfg = C2VideoConfig(work_dir=str(tmp_path / "work"))
    cfg.llm.provider = "api"
    material = pick()
    material.raw["content_scope"] = "api_summary"
    source = write_picks(tmp_path / "picks.json", [material])
    calls = []

    class FakeLLM:
        async def complete_structured(self, messages, schema, **kwargs):
            calls.append(messages)
            payload = json.loads(messages[-1]["content"].split("\n\n", 1)[1])
            assert payload["picks"][0]["body"] == BODY
            assert payload["picks"][0]["content_scope"] == "api_summary"
            assert "不能只截取第一段" in messages[-1]["content"]
            assert "不强行改写术语" in messages[0]["content"]
            return {
                "hook": "",
                "outro": "",
                "segments": [
                    {
                        "pick_id": "sample",
                        "narration": "作者认为，Agent 落地要做好失败恢复。本次内部测试不代表所有任务。",
                    }
                ],
            }

        async def close(self):
            calls.append("closed")

    monkeypatch.setattr("c2video.pipeline.script.create_llm_provider", lambda c: FakeLLM())
    result = await run_script(cfg, input_path=source, output_path=tmp_path / "model.json")
    assert calls[-1] == "closed" and len(calls) == 2
    assert result["script"].segments[0].narration.startswith("作者认为，Agent")


@pytest.mark.asyncio
async def test_missing_body_is_rejected_before_model_call(tmp_path, monkeypatch):
    cfg = C2VideoConfig(work_dir=str(tmp_path / "work"))
    cfg.llm.provider = "api"
    source = write_picks(tmp_path / "picks.json", [pick("")])
    monkeypatch.setattr(
        "c2video.pipeline.script.create_llm_provider",
        lambda c: pytest.fail("must not send title-only material to a model"),
    )
    with pytest.raises(ValueError, match="仅提供了标题"):
        await run_script(cfg, input_path=source)


@pytest.mark.parametrize("ids", [["other"], ["a", "a"], ["a"]])
def test_model_cannot_crosswire_or_omit_source_ids(ids):
    with pytest.raises(ValueError, match="pick IDs"):
        _coerce_script(
            {"segments": [{"pick_id": i, "narration": "有效口播。"} for i in ids]}, ["a", "b"]
        )


def test_root_and_packaged_prompts_match():
    packaged = Path(__file__).parents[1] / "c2video/prompts/script-prompt.md"
    assert load_prompt("script-prompt.md") == packaged.read_text()
