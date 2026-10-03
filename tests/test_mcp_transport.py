"""Real MCP handshakes over stdio/HTTP against isolated local services."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn

pytest.importorskip("mcp")

from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from c2video.mcp_server.client import StudioClient
from c2video.mcp_server.config import MCPSettings
from c2video.mcp_server.http import create_http_app
from c2video.mcp_server.server import build_server
from tests.test_mcp_server import OPERATOR, gateway

ROOT = Path(__file__).resolve().parents[1]
MCP_KEY = "mcp-http-test-" + "c" * 32


@contextmanager
def running(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False,
                                         ws="none", proxy_headers=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    try:
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "Isolated ASGI server did not start"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive(), "Test server did not stop"


def parameters(api_url):
    return StdioServerParameters(
        command=sys.executable, args=["-m", "c2video.mcp_server"], cwd=ROOT,
        env={"C2VIDEO_MCP_API_URL": api_url, "C2VIDEO_MCP_API_TOKEN": OPERATOR,
             "C2VIDEO_MCP_READ_ONLY": "false", "PYTHONDONTWRITEBYTECODE": "1",
             "PYTHON_DOTENV_DISABLED": "1"},
    )


@pytest.mark.asyncio
async def test_real_stdio_handshake_tool_call_error_and_durable_queue(tmp_path):
    app, _, _ = gateway(tmp_path)
    with running(app) as url:
        async with stdio_client(parameters(url)) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "C2Video"
                assert len((await session.list_tools()).tools) == 12
                created = await session.call_tool("c2video_create_run", {
                    "query": "stdio offline test", "mode": "demo",
                    "idempotency_key": "stdio-1",
                })
                assert not created.isError
                # AgentChat consumes TextContent, not only structuredContent.
                text = json.loads(created.content[0].text)
                assert text["run_id"] == created.structuredContent["run_id"]
                run_id = text["run_id"]
                rejected = await session.call_tool("c2video_start_run", {"run_id": run_id})
                assert rejected.isError and "CONFIRM_REQUIRED" in rejected.content[0].text
                started = await session.call_tool("c2video_start_run", {
                    "run_id": run_id, "confirmed": True,
                })
                assert not started.isError and started.structuredContent["job_status"] == "queued"
        # The stdio subprocess has exited but the existing API still owns the queue.
        assert app.state.service.store.get_run(run_id)
        with app.state.service.store.transaction() as db:
            assert db.execute("SELECT status FROM run_jobs WHERE run_id=?", (run_id,)).fetchone()[0] == "queued"


@pytest.mark.asyncio
async def test_real_streamable_http_sdk_client(tmp_path):
    app, _, _ = gateway(tmp_path)
    with running(app) as api_url:
        settings = MCPSettings(api_url=api_url, api_token=OPERATOR, http_token=MCP_KEY)
        with running(create_http_app(settings)) as mcp_url:
            async with streamablehttp_client(
                mcp_url + "/mcp", headers={"Authorization": f"Bearer {MCP_KEY}"},
            ) as (reader, writer, _):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    assert len((await session.list_tools()).tools) == 12
                    result = await session.call_tool("c2video_list_runs", {})
                    assert not result.isError and result.structuredContent["total"] == 0


def test_http_auth_body_host_origin_and_independent_keys(tmp_path):
    app, _, _ = gateway(tmp_path)
    settings = MCPSettings(api_token=OPERATOR, http_token=MCP_KEY)
    server = build_server(settings, client=StudioClient(
        settings, transport=httpx.ASGITransport(app=app),
    ))
    with TestClient(create_http_app(settings, server=server), base_url="http://127.0.0.1") as client:
        body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        }}
        headers = {"Authorization": f"Bearer {MCP_KEY}",
                   "Accept": "application/json, text/event-stream"}
        assert client.post("/mcp", json=body).status_code == 401
        assert client.post("/mcp", json=body,
                           headers={**headers, "Authorization": f"Bearer {OPERATOR}"}).status_code == 401
        assert client.post("/mcp", json=body, headers=headers).status_code == 200
        assert client.post("/mcp", json=body,
                           headers={**headers, "Host": "evil.example"}).status_code == 421
        assert client.post("/mcp", json=body,
                           headers={**headers, "Origin": "https://evil.example"}).status_code == 403
        assert client.post("/mcp", content=b"x" * 1_100_001, headers=headers).status_code == 413
    with pytest.raises(ValueError, match="32"):
        create_http_app(MCPSettings())
    with pytest.raises(ValueError, match="不同"):
        create_http_app(MCPSettings(api_token=OPERATOR, http_token=OPERATOR))


@pytest.mark.asyncio
async def test_agentchat_native_client_if_configured(tmp_path):
    """Opt-in interoperability test uses AgentChat's own venv, never its DB/LLM."""
    agent_python = os.environ.get("C2VIDEO_TEST_AGENTCHAT_PYTHON")
    backend = os.environ.get("C2VIDEO_TEST_AGENTCHAT_BACKEND")
    if not agent_python or not backend:
        pytest.skip("Set AgentChat interpreter/backend paths for native-client interoperability")
    app, _, _ = gateway(tmp_path)
    code = """
import asyncio, json, os
from agentchat.services.mcp.multi_client import MultiServerMCPClient

async def main():
    client = MultiServerMCPClient({"c2video": {
        "transport": "stdio", "command": os.environ["TEST_C2VIDEO_PYTHON"],
        "args": ["-m", "c2video.mcp_server"], "cwd": os.environ["TEST_C2VIDEO_ROOT"],
        "env": {"C2VIDEO_MCP_API_URL": os.environ["TEST_C2VIDEO_URL"],
                "C2VIDEO_MCP_API_TOKEN": os.environ["TEST_OPERATOR"],
                "C2VIDEO_MCP_READ_ONLY": "false", "PYTHON_DOTENV_DISABLED": "1",
                "PYTHONDONTWRITEBYTECODE": "1"},
    }})
    tools = {tool.name: tool for tool in await client.get_tools()}
    assert len(tools) == 12
    result = await tools["c2video_create_run"].ainvoke({
        "query": "AgentChat native offline", "mode": "demo", "idempotency_key": "agentchat-1",
    })
    data = json.loads(result)
    progress = json.loads(await tools["c2video_get_run"].ainvoke({"run_id": data["run_id"]}))
    assert progress["state"] == "PLAN"
    print(json.dumps({"tools": len(tools), "run_id": data["run_id"], "state": progress["state"]}))

asyncio.run(main())
"""
    with running(app) as url:
        proc = await asyncio.create_subprocess_exec(
            agent_python, "-c", code, cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": backend, "PYTHONDONTWRITEBYTECODE": "1",
                 "TEST_C2VIDEO_PYTHON": sys.executable, "TEST_C2VIDEO_ROOT": str(ROOT),
                 "TEST_C2VIDEO_URL": url, "TEST_OPERATOR": OPERATOR},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            pytest.fail("AgentChat native-client test timed out")
        assert proc.returncode == 0, stderr.decode()[-4000:]
        report = json.loads(stdout.decode().strip().splitlines()[-1])
        assert report["tools"] == 12 and report["state"] == "PLAN"
        assert len(app.state.service.list_runs()) == 1
