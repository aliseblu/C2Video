"""Model editorial gate and local fallback bad cases (all synthetic)."""

import pytest

from c2video.config.schema import C2VideoConfig
from c2video.pipeline.io import write_picks
from c2video.pipeline.keypoints import summarize_keypoints
from c2video.pipeline.models import DigestScript, Pick, ScriptSegment
from c2video.pipeline.script import _editorial_feedback, run_script


def test_explicit_core_point_after_but_is_not_tied_to_intro():
    text = "过去我们花了很长时间研究工具的发展历史和行业背景。"
    text += "但是我的一个核心观点是，可靠性比堆叠功能更重要。"
    assert summarize_keypoints(text, budget=25) == "可靠性比堆叠功能更重要。"


def test_directory_is_not_mistaken_for_answers():
    with pytest.raises(ValueError, match="目录"):
        summarize_keypoints("分类目录：" + "这个问题怎么解决？" * 7, title="我的答题索引")


def test_uncompressible_long_body_has_actionable_error():
    with pytest.raises(ValueError, match="配置模型"):
        summarize_keypoints("无标点的待整理文本" * 100, budget=40)


def test_model_cannot_read_a_long_opening_verbatim():
    body = "我们在整理工具链并观察每个环节的行为，" * 20
    pick = Pick(id="a", text=body)
    script = DigestScript(segments=[ScriptSegment(pick_id="a", narration=body[:150])])
    assert any("照读开头" in text for text in _editorial_feedback(script, [pick]))


@pytest.mark.asyncio
@pytest.mark.parametrize("repairs", [True, False])
async def test_model_style_rewrite_is_bounded_and_never_falls_back_to_local(
    tmp_path, monkeypatch, repairs
):
    cfg = C2VideoConfig(work_dir=str(tmp_path / "work"))
    cfg.llm.provider = "api"
    source = write_picks(tmp_path / "picks.json", [Pick(id="a", text="工具失败需要恢复流程。")])
    calls = []

    class Model:
        async def complete_structured(self, messages, schema, **kwargs):
            calls.append(list(messages))
            good = repairs and len(calls) == 2
            return {
                "hook": "",
                "outro": "",
                "segments": [
                    {
                        "pick_id": "a",
                        "narration": "工具失败需要恢复流程。"
                        if good
                        else "第1条，今天给大家介绍这篇文章。",
                    }
                ],
            }

        async def close(self):
            calls.append("closed")

    monkeypatch.setattr("c2video.pipeline.script.create_llm_provider", lambda c: Model())
    if repairs:
        result = await run_script(cfg, input_path=source, output_path=tmp_path / "script.json")
        assert result["script"].segments[0].narration.startswith("工具失败")
    else:
        with pytest.raises(ValueError, match="仍未满足概括要求"):
            await run_script(cfg, input_path=source, output_path=tmp_path / "script.json")
        assert not (tmp_path / "script.json").exists()
    assert len(calls) == 3 and calls[-1] == "closed"
    assert "请仅修订这份口播" in calls[1][-1]["content"]
