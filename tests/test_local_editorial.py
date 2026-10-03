"""Account-free local curation and script generation."""

from c2video.pipeline.card import build_card_html
from c2video.pipeline.curate import score_candidates_locally
from c2video.pipeline.script import build_local_script
from c2video.source.models import CandidateItem


def _github_candidate() -> CandidateItem:
    return CandidateItem(
        id="github-1",
        text="acme/agent: Open source AI workflow tool",
        author_name="acme",
        author_username="acme/agent",
        likes=3200,
        shares=240,
        replies=18,
        url="https://github.com/acme/agent",
        raw={
            "source": "github",
            "source_type": "repository",
            "full_name": "acme/agent",
            "description": "Open source AI workflow tool",
            "language": "Python",
            "topics": ["agents", "rag"],
        },
    )


def test_local_editorial_scores_and_writes_source_aware_script() -> None:
    picks = score_candidates_locally(
        [_github_candidate()],
        exclude_pick_ids=[],
        top_n=3,
        theme="AI agent",
    )
    assert len(picks) == 1
    assert picks[0].score >= 6.5
    assert picks[0].reason.startswith("本地规则")

    script = build_local_script(picks, target_duration_seconds=60)
    assert len(script.segments) == 1
    assert "GitHub" in script.segments[0].narration
    assert "Star" in script.segments[0].narration

    compact = build_local_script(picks * 2, target_duration_seconds=30)
    assert sum(len(item.narration) for item in compact.segments) < 260
    assert "Python" in compact.segments[0].narration
    assert "Open source" not in compact.segments[0].narration


def test_public_card_uses_github_metrics() -> None:
    pick = score_candidates_locally(
        [_github_candidate()], exclude_pick_ids=[], top_n=1
    )[0]
    doc = build_card_html(pick)
    assert "stars" in doc
    assert "forks" in doc
    assert "公开来源摘要" in doc
    assert "likes" not in doc

