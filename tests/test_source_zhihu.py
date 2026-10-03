"""Offline contract tests based on official Zhihu API docs; not live API proof."""

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from c2video.api.app import create_app
from c2video.application import ApplicationService
from c2video.config.schema import C2VideoConfig, HardFilterConfig, SourceConfig
from c2video.pipeline.card import build_card_html
from c2video.pipeline.curate import score_candidates_locally
from c2video.pipeline.hard_filter import is_fresh
from c2video.pipeline.script import build_local_script
from c2video.source.factory import create_source
from c2video.source.models import CandidateItem
from c2video.source.status import source_status
from c2video.source.zhihu import ZhihuSource, normalize_item, parse_materials, read_materials
from c2video.tools.base import ToolContext
from c2video.tools.legacy_pipeline import _isolate_config


def material(**extra):
    return {"title": "如何评估 AI Agent？", "content": "这是离线测试素材，不是真实知乎内容。",
            "url": "https://www.zhihu.com/question/100/answer/200", "author": "测试作者", **extra}


def official(**extra):
    return {"Title": "AI Agent 评测", "ContentText": "<em>测试</em>内容摘要。",
            "Url": "https://zhuanlan.zhihu.com/p/100?utm_medium=openapi_platform",
            "ContentType": "Article", "ContentID": "100", "AuthorName": "测试作者",
            "VoteUpCount": 12, "CommentCount": 3,
            "EditTime": int(datetime.now(timezone.utc).timestamp()), **extra}


def test_official_mapping_preserves_attribution_and_metrics():
    item = normalize_item(official(), acquisition="api")
    assert item.likes == 12 and item.replies == 3
    assert item.shares == 0 and item.favorites == 0 and item.views == 0
    assert item.raw["source"] == "zhihu" and item.raw["source_type"] == "zhihu_article"
    assert item.author_name == "测试作者"
    assert "utm_medium" in item.url and "<em>" not in item.text
    assert item.raw["content_scope"] == "api_summary"


def test_materials_deduplicate_links_without_query():
    items = parse_materials([material(), material(url=material()["url"] + "?utm_source=test")])
    assert len(items) == 1 and items[0].created_at == ""


@pytest.mark.parametrize("url", ["https://evil.test/question/1", "http://www.zhihu.com/question/1",
    "https://www.zhihu.com.evil.test/question/1", "https://www.zhihu.com@evil.test/question/1",
    "file:///etc/passwd", "https://www.zhihu.com/people/alice", "https://127.0.0.1/question/1"])
def test_import_rejects_non_content_or_foreign_urls(url):
    with pytest.raises(ValueError, match="原文链接"):
        parse_materials([material(url=url)])


@pytest.mark.parametrize("data", [[], {}, ["text"], [material(title="")], [material()] * 101])
def test_invalid_import_fails_instead_of_silently_using_demo(data):
    with pytest.raises(ValueError):
        parse_materials(data)


def test_read_materials_size_json_and_missing_errors(tmp_path):
    with pytest.raises(ValueError, match="尚未导入"):
        read_materials(tmp_path / "missing.json")
    path = tmp_path / "data.json"
    path.write_text("not json")
    with pytest.raises(ValueError, match="JSON"):
        read_materials(path)
    path.write_bytes(b"a" * 1_000_001)
    with pytest.raises(ValueError, match="1 MB"):
        read_materials(path)


def test_official_request_is_bounded_and_authenticated():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.host == "developer.zhihu.com"
        assert request.url.path.endswith("/zhihu_search")
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert request.headers["X-Request-Timestamp"].isdigit()
        assert int(request.url.params["Count"]) <= 10
        return httpx.Response(200, json={"Code": 0, "Data": {"Items": [official()]}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = ZhihuSource(access_secret="test-secret", http_client=client)
        assert len(source.fetch(["AI", "AI", "Agent"], max_results=1)) == 1
    assert len(calls) == 1


@pytest.mark.parametrize("status,body", [(401, {}), (403, {}), (429, {}),
    (200, {"Code": 20001}), (200, {"Code": 30002}), (200, {"Code": 0, "Data": {}})])
def test_api_failure_never_becomes_empty_success(status, body):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body))) as client:
        with pytest.raises(RuntimeError):
            ZhihuSource(access_secret="secret", http_client=client).fetch(["AI"])


def test_redirect_does_not_forward_secret():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.test/collect"})
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(RuntimeError):
            ZhihuSource(access_secret="secret", http_client=client).fetch(["AI"])
    assert len(calls) == 1


def test_hot_list_missing_metrics_and_dates_are_not_fabricated():
    def handler(request):
        assert request.url.path.endswith("/hot_list") and request.url.params["Limit"] == "30"
        return httpx.Response(200, json={"Code": 0, "Data": {"Items": [
            {"Title": "AI 模型有什么进展？", "Url": "https://www.zhihu.com/question/1",
             "Summary": "", "ThumbnailUrl": ""}]}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        items = ZhihuSource(method="hot_list", access_secret="secret", http_client=client).fetch(["AI"])
    item = items[0]
    assert item.created_at == "" and item.author_name == "" and not item.raw["available_metrics"]
    picks = score_candidates_locally(items, exclude_pick_ids=[], top_n=1)
    assert "未提供互动数据" in build_card_html(picks[0])
    with pytest.raises(ValueError, match="仅提供了标题"):
        build_local_script(picks)


def test_import_works_without_secret_and_keeps_old_date(tmp_path):
    path = tmp_path / "materials.json"
    path.write_text(json.dumps([material(created_at="2020-01-01T00:00:00Z")]))
    items = ZhihuSource(mode="import", import_file=str(path)).fetch(["不匹配也保留人工选材"])
    assert len(items) == 1 and items[0].created_at.startswith("2020")
    assert is_fresh(items[0], 24)


def test_source_factory_defaults_and_missing_secret(tmp_path):
    cfg = C2VideoConfig()
    assert isinstance(create_source(cfg), ZhihuSource)
    assert not source_status(cfg)["configured"]
    with pytest.raises(ValueError, match="Access Secret"):
        create_source(cfg).fetch(["AI"])
    service = ApplicationService(work_dir=str(tmp_path), config=cfg)
    with pytest.raises(ValueError, match="Access Secret"):
        service.create_run(query="AI")
    assert service.list_runs() == []


def test_api_import_snapshot_survives_worker_and_is_run_scoped(tmp_path):
    cfg = C2VideoConfig(work_dir=str(tmp_path))
    app = create_app(work_dir=str(tmp_path), config=cfg)
    client = TestClient(app)
    response = client.post("/api/runs", json={"query": "AI", "mode": "live", "materials": [material()]})
    assert response.status_code == 201
    run_id = response.json()["run"]["run_id"]
    path = tmp_path / "agent_runs" / run_id / "source.zhihu.json"
    assert path.is_file()
    isolated = _isolate_config(cfg, ToolContext(run_id=run_id, task_id="t", work_dir=str(tmp_path)))
    assert isolated.source.zhihu_mode == "import"
    assert len(create_source(isolated).fetch(["AI"])) == 1
    assert cfg.source.zhihu_mode == "api"
    other = _isolate_config(cfg, ToolContext(run_id="run_other", task_id="t", work_dir=str(tmp_path)))
    assert other.source.zhihu_mode == "api"


def test_invalid_materials_do_not_create_a_run(tmp_path):
    client = TestClient(create_app(work_dir=str(tmp_path), config=C2VideoConfig()))
    assert client.post("/api/runs", json={"query": "AI", "materials": []}).status_code == 409
    assert client.get("/api/runs").json()["items"] == []


def test_secret_never_in_health_or_serialized_configuration(tmp_path):
    cfg = C2VideoConfig(source=SourceConfig(zhihu_access_secret="do-not-leak"))
    client = TestClient(create_app(work_dir=str(tmp_path), config=cfg))
    health = client.get("/api/health")
    assert "do-not-leak" not in health.text + cfg.model_dump_json() + repr(cfg)
    assert health.json()["source_configured"] is True
    assert "待验证" in health.json()["source_detail"]


def test_old_metric_artifacts_remain_readable():
    assert CandidateItem.model_validate({"id": "old", "text": "old", "retweets": 9}).shares == 9
    assert HardFilterConfig.model_validate({"min_retweets": 3}).min_shares == 3
    assert "retweets" not in CandidateItem(id="new", text="new").model_dump()

def test_unknown_date_is_not_replaced_with_today_in_video_or_credits(tmp_path):
    from c2video.pipeline.render import digest_date_label, write_publish_md
    items = parse_materials([material()])
    picks = score_candidates_locally(items, exclude_pick_ids=[], top_n=1)
    assert digest_date_label(picks) == "发布日期未提供"
    page = build_card_html(picks[0], show_date=True)
    assert '<div class="date"></div>' in page
    destination = tmp_path / "publish.md"
    write_publish_md(destination, script=build_local_script(picks),
                     video=tmp_path / "video.mp4", cover=tmp_path / "cover.png", picks=picks)
    assert "发布时间未提供 测试作者" in destination.read_text()


