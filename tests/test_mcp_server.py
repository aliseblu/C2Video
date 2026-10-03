"""MCP tools -> existing API -> isolated SQLite; no model/network service calls."""

from __future__ import annotations

import json

import httpx
import pytest

pytest.importorskip("mcp")

from mcp.server.fastmcp.exceptions import ToolError

from c2video.api.access import PlatformSettings
from c2video.api.app import create_app
from c2video.domain.models import MemoryCandidate, RunState
from c2video.mcp_server.client import StudioClient
from c2video.mcp_server.config import MCPSettings
from c2video.mcp_server.server import build_server

OPERATOR = "mcp-test-operator-" + "a" * 32
VIEWER = "mcp-test-viewer-" + "b" * 32


def gateway(tmp_path, *, read_only=False, token=OPERATOR):
    app = create_app(work_dir=str(tmp_path), platform=PlatformSettings(
        operator_token=OPERATOR, viewer_token=VIEWER, worker_mode="off",
    ))
    settings = MCPSettings(api_token=token, read_only=read_only)
    client = StudioClient(settings, transport=httpx.ASGITransport(app=app))
    return app, client, build_server(settings, client=client)


async def call(server, name, **arguments):
    result = await server.call_tool(name, arguments)
    if isinstance(result, tuple):
        return result[1]
    if isinstance(result, dict):
        return result
    return json.loads(result[0].text)


@pytest.mark.asyncio
async def test_discovery_input_schemas_and_read_only_surface(tmp_path):
    _, _, server = gateway(tmp_path)
    tools = await server.list_tools()
    assert len(tools) == 12
    mapping = {tool.name: tool for tool in tools}
    assert not any("approve" in name or "shell" in name for name in mapping)
    schema = mapping["c2video_create_run"].inputSchema
    assert "idempotency_key" in schema["required"]
    assert schema["properties"]["autonomy"]["enum"] == ["supervised", "assisted"]
    assert mapping["c2video_start_run"].annotations.idempotentHint is True
    assert mapping["c2video_submit_feedback"].annotations.idempotentHint is False
    _, _, readonly = gateway(tmp_path / "readonly", read_only=True)
    readonly_tools = await readonly.list_tools()
    assert len(readonly_tools) == 7
    assert all(tool.annotations.readOnlyHint for tool in readonly_tools)


@pytest.mark.asyncio
async def test_create_and_start_are_separate_idempotent_and_persistent(tmp_path):
    app, client, server = gateway(tmp_path)
    args = {"query": "离线测试", "mode": "demo", "idempotency_key": "mcp-create-1"}
    first = await call(server, "c2video_create_run", **args)
    second = await call(server, "c2video_create_run", **args)
    assert first["run_id"] == second["run_id"]
    assert len(app.state.service.list_runs()) == 1
    platform = await client.request("GET", "/api/platform")
    assert platform["counts"].get("queued", 0) == 0
    with pytest.raises(ToolError, match="409"):
        await call(server, "c2video_create_run", **{**args, "query": "different"})
    with pytest.raises(ToolError, match="CONFIRM_REQUIRED"):
        await call(server, "c2video_start_run", run_id=first["run_id"])
    for _ in range(2):
        queued = await call(server, "c2video_start_run", run_id=first["run_id"], confirmed=True)
        assert queued["job_status"] == "queued"
    assert (await client.request("GET", "/api/platform"))["counts"]["queued"] == 1
    # A fresh gateway connection sees the same task and queue; no in-memory jobs.
    new_server = build_server(client.settings, client=StudioClient(
        client.settings, transport=httpx.ASGITransport(app=app),
    ))
    status = await call(new_server, "c2video_get_run", run_id=first["run_id"])
    assert status["run_id"] == first["run_id"] and status["progress"]["total_tasks"] > 0


@pytest.mark.asyncio
async def test_live_never_silently_becomes_demo(tmp_path):
    app, _, server = gateway(tmp_path)
    with pytest.raises(ToolError, match="409"):
        await call(server, "c2video_create_run", query="真实选题", idempotency_key="live-1")
    assert app.state.service.list_runs() == []


@pytest.mark.asyncio
async def test_gate_review_cannot_approve_and_resume_requires_enqueue(tmp_path):
    app, _, server = gateway(tmp_path)
    created = await call(server, "c2video_create_run", query="demo", mode="demo", idempotency_key="gate-1")
    run_id = created["run_id"]
    await app.state.service.execute(run_id)
    review = await call(server, "c2video_get_review", run_id=run_id)
    assert review["waiting_gates"] and review["state"] == "WAIT_GATE_1"
    assert "人工审核" in review["next_action"]
    with pytest.raises(ToolError):
        await call(server, "c2video_control_run", run_id=run_id, action="approve_gate", confirmed=True)
    app.state.service.action(run_id, "approve_gate", {"summary": "isolated human approval"})
    await call(server, "c2video_control_run", run_id=run_id, action="pause")
    resumed = await call(server, "c2video_control_run", run_id=run_id, action="resume")
    assert "c2video_start_run" in resumed["next_action"]
    with pytest.raises(ToolError, match="CONFIRM_REQUIRED"):
        await call(server, "c2video_control_run", run_id=run_id, action="cancel")
    canceled = await call(server, "c2video_control_run", run_id=run_id, action="cancel", confirmed=True)
    assert canceled["state"] == "CANCELED"


@pytest.mark.asyncio
async def test_publish_files_do_not_imply_approval_and_links_have_no_credentials(tmp_path):
    app, client, server = gateway(tmp_path)
    created = await call(server, "c2video_create_run", query="demo", mode="demo", idempotency_key="publish-1")
    run_id = created["run_id"]
    folder = tmp_path / "agent_runs" / run_id / "publish_kit"
    folder.mkdir(parents=True)
    (folder / "video.mp4").write_bytes(b"isolated test fixture; not a real video")
    (folder / "cover.png").write_bytes(b"isolated cover fixture")
    (folder / "publish.md").write_text("# Test publish copy", encoding="utf-8")
    result = await call(server, "c2video_get_publish_kit", run_id=run_id)
    assert result["ready"] is False and set(result["files"]) == {"video", "cover", "publish"}
    assert all("?" not in link and OPERATOR not in link for link in result["files"].values())
    app.state.service.store.set_run_state(run_id, RunState.COMPLETE)
    assert (await call(server, "c2video_get_publish_kit", run_id=run_id))["ready"] is True
    async with httpx.AsyncClient(transport=client.transport, base_url=client.settings.api_url) as http:
        assert (await http.get(f"/api/runs/{run_id}/media/publish")).status_code == 401
        doc = await http.get(f"/api/runs/{run_id}/media/publish",
                            headers={"Authorization": f"Bearer {OPERATOR}"})
        assert doc.status_code == 200 and doc.text == "# Test publish copy"
        assert (await http.get(f"/api/runs/{run_id}/media/secret",
                              headers={"Authorization": f"Bearer {OPERATOR}"})).status_code == 404


@pytest.mark.asyncio
async def test_upstream_viewer_cannot_write_even_if_gateway_exposes_tools(tmp_path):
    app, _, server = gateway(tmp_path, token=VIEWER)
    assert (await call(server, "c2video_list_runs"))["total"] == 0
    with pytest.raises(ToolError, match="403"):
        await call(server, "c2video_create_run", query="demo", mode="demo", idempotency_key="denied")
    assert app.state.service.list_runs() == []


@pytest.mark.asyncio
async def test_feedback_confirms_ai_and_keeps_partial_success(tmp_path):
    app, _, server = gateway(tmp_path)
    created = await call(server, "c2video_create_run", query="demo", mode="demo", idempotency_key="feedback-1")
    with pytest.raises(ToolError, match="CONFIRM_REQUIRED"):
        await call(server, "c2video_submit_feedback", run_id=created["run_id"], comment="以后口播简洁")
    result = await call(server, "c2video_submit_feedback",
                        run_id=created["run_id"], comment="以后口播简洁", confirmed=True)
    assert result["feedback_id"] and result["memory_processing"]["status"] == "unavailable"
    assert app.state.service.store.list_memories() == []
    with app.state.service.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM feedback").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_memories_and_usage_keep_existing_semantics(tmp_path):
    app, _, server = gateway(tmp_path)
    run = app.state.service.create_run(query="test", mode="demo")
    memory = MemoryCandidate(run_id=run["run"]["run_id"], content="未经 AI 处理的原文", memory_type="preference")
    app.state.service.store.add_memory(memory)
    assert (await call(server, "c2video_list_memories"))["total"] == 1
    with pytest.raises(ToolError, match="409"):
        await call(server, "c2video_set_memory_status", memory_id=memory.memory_id, status="approved")
    stopped = await call(server, "c2video_set_memory_status", memory_id=memory.memory_id, status="rejected")
    assert stopped["status"] == "rejected"
    usage = await call(server, "c2video_get_usage")
    assert usage["totals"]["calls"] == 0
    assert usage["totals"]["total_tokens"] is None

@pytest.mark.asyncio
async def test_wide_review_has_total_context_limit():
    from c2video.mcp_server.server import invoke

    async def oversized():
        return {"run_id": "run_test", "state": "WAIT_GATE_1",
                "studio_url": "http://127.0.0.1:8765/runs/run_test",
                "evidence": [{"body": "x" * 4000} for _ in range(30)]}

    result = await invoke(oversized())
    assert result["_truncated"] is True
    assert result["run_id"] == "run_test" and "evidence" not in result
    assert len(json.dumps(result)) < 64_000
