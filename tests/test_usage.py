"""Real provider transport, frozen HTTP responses, isolated usage database."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from c2video.api.app import create_app
from c2video.config.schema import C2VideoConfig, LLMConfig
from c2video.llm.api_impl import APICompatibleLLMProvider
from c2video.storage.run_store import RunStore
from c2video.usage import record_usage, usage_dashboard, usage_values


def config_for(path, **kwargs):
    RunStore(path)
    return LLMConfig(provider="api", api_base_url="https://example.invalid/v1",
                     api_key="test-key-not-for-logs", model="test-model",
                     telemetry_db_path=str(path), telemetry_purpose="memory", **kwargs)


def response(usage=None, content="ok"):
    data = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        data["usage"] = usage
    return data


async def provider_with(config, handler):
    provider = APICompatibleLLMProvider(config)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(base_url=config.api_base_url,
                                        transport=httpx.MockTransport(handler))
    return provider


@pytest.mark.asyncio
async def test_usage_is_recorded_from_actual_response_not_text_length(tmp_path):
    cfg = config_for(tmp_path / "agent.db", telemetry_run_id="run_attribution")
    provider = await provider_with(cfg, lambda request: httpx.Response(200, json=response({
        "prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168,
        "prompt_tokens_details": {"cached_tokens": 32}, "cost_usd": 0.001,
    }, content="PRIVATE MODEL RESPONSE")))
    try:
        assert await provider.complete([{"role": "user", "content": "PRIVATE USER PROMPT"}]) == "PRIVATE MODEL RESPONSE"
    finally:
        await provider.close()
    report = usage_dashboard(tmp_path / "agent.db")
    assert report["totals"]["calls"] == 1
    assert report["totals"]["input_tokens"] == 123
    assert report["totals"]["output_tokens"] == 45
    assert report["totals"]["total_tokens"] == 168
    assert report["totals"]["cached_input_tokens"] == 32
    assert report["totals"]["reported_cost_usd"] == 0.001
    assert report["totals"]["estimated_cost_usd"] is None
    assert report["recent"][0]["run_id"] == "run_attribution"
    assert report["recent"][0]["purpose"] == "memory"
    serialized = json.dumps(report)
    for text in ("PRIVATE USER PROMPT", "PRIVATE MODEL RESPONSE", "test-key-not-for-logs"):
        assert text not in serialized


@pytest.mark.asyncio
async def test_every_format_fallback_and_failed_request_is_counted(tmp_path):
    cfg = config_for(tmp_path / "agent.db", input_usd_per_million=2, output_usd_per_million=4)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(400, json={"error": "schema unsupported",
                                            "usage": {"input_tokens": 2, "output_tokens": 0}})
        if len(requests) == 2:
            return httpx.Response(400, json={"error": "json_object unsupported"})
        return httpx.Response(200, json=response({"input_tokens": 10, "output_tokens": 5}, content="{}"))

    provider = await provider_with(cfg, handler)
    try:
        assert await provider.complete_structured([], {"type": "object"}) == {}
    finally:
        await provider.close()
    totals = usage_dashboard(tmp_path / "agent.db")["totals"]
    assert totals["calls"] == 3 and totals["failed_calls"] == 2
    assert totals["total_tokens"] == 17
    assert totals["unknown_usage_calls"] == 1
    assert totals["unpriced_calls"] == 1
    assert totals["estimated_cost_usd"] == pytest.approx(0.000044)
    assert totals["reported_cost_usd"] is None


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": True, "completion_tokens": "25"},
                                        {"prompt_tokens": -1, "completion_tokens": 1.5}])
def test_missing_or_invalid_usage_is_unknown_not_zero(tmp_path, usage):
    cfg = config_for(tmp_path / "agent.db")
    record_usage(cfg, data=response(usage), status="succeeded", http_status=200, latency_ms=10, error_type=None)
    totals = usage_dashboard(tmp_path / "agent.db")["totals"]
    assert totals["total_tokens"] is None
    assert totals["unknown_usage_calls"] == 1
    assert totals["unpriced_calls"] == 1


def test_usage_zero_is_distinct_from_unknown_and_cost_units_are_explicit(tmp_path):
    cfg = config_for(tmp_path / "agent.db", input_usd_per_million=2, output_usd_per_million=3)
    zero = usage_values({"usage": {"input_tokens": 0, "output_tokens": 0}}, cfg)
    assert zero["total_tokens"] == 0 and zero["estimated_cost_usd"] == 0
    unknown_currency = usage_values({"usage": {"cost": 3.5}}, cfg)
    assert unknown_currency["reported_cost_usd"] is None
    reported = usage_values({"usage": {"cost_usd": 0, "prompt_tokens": 10, "completion_tokens": 10}}, cfg)
    assert reported["reported_cost_usd"] == 0 and reported["estimated_cost_usd"] is None
    assert usage_values({"usage": {"cost_usd": float("nan")}}, cfg)["reported_cost_usd"] is None
    for price in (-1, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            LLMConfig(input_usd_per_million=price)


@pytest.mark.asyncio
@pytest.mark.parametrize("canceled", [False, True])
async def test_timeout_and_cancellation_are_recorded_without_made_up_tokens(tmp_path, canceled):
    cfg = config_for(tmp_path / "agent.db")

    async def handler(request):
        if canceled:
            raise asyncio.CancelledError()
        raise httpx.ReadTimeout("not for logs", request=request)

    provider = await provider_with(cfg, handler)
    try:
        with pytest.raises(asyncio.CancelledError if canceled else httpx.ReadTimeout):
            await provider.complete([])
    finally:
        await provider.close()
    report = usage_dashboard(tmp_path / "agent.db")
    assert report["totals"]["calls"] == 1
    assert report["totals"]["total_tokens"] is None
    assert report["recent"][0]["status"] == ("canceled" if canceled else "failed")
    assert "not for logs" not in json.dumps(report)


@pytest.mark.asyncio
async def test_telemetry_failure_does_not_repeat_a_paid_call(tmp_path, monkeypatch, caplog):
    cfg = config_for(tmp_path / "agent.db")

    def broken(*args, **kwargs):
        raise OSError("private path or secret must not be logged")

    monkeypatch.setattr("c2video.llm.api_impl.record_usage", broken)
    provider = await provider_with(cfg, lambda request: httpx.Response(200, json=response()))
    try:
        assert await provider.complete([]) == "ok"
    finally:
        await provider.close()
    assert "usage could not be recorded" in caplog.text
    assert "private path" not in caplog.text


def test_dashboard_range_breakdown_and_empty_state(tmp_path):
    path = tmp_path / "agent.db"
    cfg = config_for(path)
    empty = usage_dashboard(path)
    assert empty["totals"]["calls"] == 0 and empty["totals"]["total_tokens"] is None
    assert len(empty["daily"]) == 7 and empty["recent"] == []
    record_usage(cfg, data=response({"input_tokens": 5, "output_tokens": 2}), status="succeeded",
                 http_status=200, latency_ms=200, error_type=None)
    old = (datetime.now(UTC) - timedelta(days=12)).isoformat()
    store = RunStore(path)
    with store.transaction() as db:
        db.execute("UPDATE llm_usage SET created_at=?", (old,))
    assert usage_dashboard(path, days=7)["totals"]["calls"] == 0
    month = usage_dashboard(path, days=30)
    assert month["totals"]["calls"] == 1
    assert month["models"][0]["model"] == "test-model"
    assert month["purposes"][0]["purpose"] == "memory"
    assert month["totals"]["average_latency_ms"] == 200
    assert len(month["daily"]) == 30


def test_api_summary_and_run_memory_overview(tmp_path):
    cfg = C2VideoConfig(llm=config_for(tmp_path / "other.db"))
    app = create_app(work_dir=str(tmp_path / "work"), config=cfg)
    client = TestClient(app)
    run_id = client.post("/api/runs", json={"query": "test", "mode": "demo"}).json()["run"]["run_id"]
    actual = app.state.service.config.llm
    assert actual.telemetry_db_path == app.state.service.db_path
    record_usage(actual.model_copy(update={"telemetry_run_id": run_id, "telemetry_purpose": "script"}),
                 data=response({"prompt_tokens": 2, "completion_tokens": 3}), status="succeeded",
                 http_status=200, latency_ms=50, error_type=None)
    result = client.get("/api/usage?days=7")
    assert result.status_code == 200
    assert result.json()["totals"]["total_tokens"] == 5
    assert result.json()["runs"] == {"PLAN": 1}
    assert result.json()["memories"]["approved_now"] == 0
    for invalid in (0, 8, 999, "abc"):
        assert client.get(f"/api/usage?days={invalid}").status_code == 422
