"""Gateway boundaries: offline HTTP responses, never real Studio credentials."""

from __future__ import annotations

import json

import httpx
import pytest

from c2video.mcp_server.client import GatewayError, StudioClient, bounded
from c2video.mcp_server.config import MCPSettings


@pytest.mark.parametrize("url", [
    "file:///tmp/private", "http://user:SECRET@localhost:8765", "http://127.0.0.1/api",
    "http://localhost:8765?api_key=SECRET", "http://localhost:99999",
    "http://remote.example:8765", "http://localhost:8765/#SECRET",
])
def test_endpoint_configuration_fails_closed(url):
    with pytest.raises(ValueError) as error:
        MCPSettings(api_url=url)
    assert "SECRET" not in str(error.value)


def test_settings_never_print_tokens():
    settings = MCPSettings(api_token="PRIVATE-STUDIO-KEY", http_token="PRIVATE-MCP-KEY")
    assert "PRIVATE" not in repr(settings)
    assert settings.public_url == settings.api_url
    with pytest.raises(ValueError):
        MCPSettings(api_token="secret\nheader")


def test_bounded_output_marks_truncation_and_preserves_usage_unknowns():
    result = bounded({
        "input_tokens": 125, "output_tokens": None, "total_tokens": None,
        "api_key": "SECRET", "comment": "Bearer SECRET-key-value",
        "items": list(range(45)), "body": "x" * 4500,
    })
    assert result["input_tokens"] == 125 and result["total_tokens"] is None
    assert result["output_tokens"] is None
    assert result["_truncated"] is True and len(result["items"]) == 30
    assert len(result["body"]) == 4000
    assert "SECRET" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 401, 403, 404, 409, 422, 429, 500])
async def test_upstream_errors_do_not_leak_or_retry(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"location": "https://evil.invalid/SECRET"},
                              json={"detail": "SECRET raw internal response"})

    client = StudioClient(MCPSettings(api_token="PRIVATE-KEY"),
                          transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayError) as error:
        await client.request("POST", "/api/runs", body={"query": "test"}, idempotency_key="same-key")
    assert f"STUDIO_HTTP_{status}" in str(error.value)
    assert "SECRET" not in str(error.value) and "PRIVATE" not in str(error.value)
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer PRIVATE-KEY"
    assert requests[0].headers["idempotency-key"] == "same-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ConnectError])
async def test_ambiguous_writes_warn_without_retry(failure):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise failure("SECRET endpoint or response", request=request)

    client = StudioClient(MCPSettings(), transport=httpx.MockTransport(handler))
    with pytest.raises(GatewayError) as error:
        await client.request("POST", "/api/runs/run_test/feedback", body={"comment": "test"})
    assert "SECRET" not in str(error.value) and "重试" in str(error.value)
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body,content_type", [
    ("<!doctype html>SECRET", "text/html"),
    ("SECRET-not-json", "application/json"),
    ("[]", "application/json"),
])
async def test_html_invalid_json_and_wrong_shape_fail_clearly(body, content_type):
    client = StudioClient(MCPSettings(), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=body, headers={"content-type": content_type}),
    ))
    with pytest.raises(GatewayError, match="INVALID_RESPONSE") as error:
        await client.request("GET", "/api/runs")
    assert "SECRET" not in str(error.value)


@pytest.mark.asyncio
async def test_transport_byte_limits(monkeypatch):
    client = StudioClient(MCPSettings(), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"large": "x" * 101}),
    ))
    monkeypatch.setattr("c2video.mcp_server.client.MAX_RESPONSE_BYTES", 100)
    with pytest.raises(GatewayError, match="RESPONSE_TOO_LARGE"):
        await client.request("GET", "/api/runs")
    monkeypatch.setattr("c2video.mcp_server.client.MAX_REQUEST_BYTES", 100)
    with pytest.raises(GatewayError, match="INPUT_TOO_LARGE"):
        await client.request("POST", "/api/runs", body={"query": "x" * 101})


@pytest.mark.asyncio
@pytest.mark.parametrize("run_id", ["../other", "run_a/../../secret", "https://evil.invalid", "run_a?key=x"])
async def test_run_id_cannot_become_a_path_or_endpoint(run_id):
    client = StudioClient(MCPSettings())
    with pytest.raises(GatewayError, match="INVALID_ID"):
        await client.snapshot(run_id)


@pytest.mark.asyncio
async def test_read_only_client_rejects_writes_before_network():
    client = StudioClient(MCPSettings(read_only=True))
    with pytest.raises(GatewayError, match="READ_ONLY"):
        await client.request("POST", "/api/runs", body={"query": "test"})


def test_cli_url_override_also_updates_default_media_links(monkeypatch, capsys):
    from c2video.mcp_server.__main__ import main

    monkeypatch.delenv("C2VIDEO_MCP_PUBLIC_URL", raising=False)
    monkeypatch.setenv("C2VIDEO_MCP_API_URL", "http://127.0.0.1:8765")

    async def health(self):
        return {"public_url": self.settings.public_url}

    monkeypatch.setattr(StudioClient, "health", health)
    main(["--api-url", "http://127.0.0.1:9876", "--check"])
    assert json.loads(capsys.readouterr().out)["public_url"] == "http://127.0.0.1:9876"
