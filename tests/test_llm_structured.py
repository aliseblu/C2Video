"""Exercise real request formatting with an offline HTTP transport."""

from __future__ import annotations

import copy
import json

import httpx
import pytest

from c2video.config.schema import LLMConfig
from c2video.llm.api_impl import (
    APICompatibleLLMProvider,
    LLMHTTPError,
    LLMResponseError,
    LLMStructuredOutputError,
)
from c2video.memory import MemoryExtraction

SCHEMA = MemoryExtraction.model_json_schema()
MESSAGES = [
    {"role": "system", "content": "Extract durable preferences using the requested JSON schema."},
    {"role": "user", "content": "以后口播保持简洁。"},
]


async def provider_for(handler):
    provider = APICompatibleLLMProvider(LLMConfig(
        provider="api", api_base_url="https://example.invalid/v1",
        api_key="offline-test-key", model="offline-model",
    ))
    await provider.close()
    provider._client = httpx.AsyncClient(
        base_url="https://example.invalid/v1", transport=httpx.MockTransport(handler),
    )
    return provider


def completion(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.mark.asyncio
@pytest.mark.parametrize("fallbacks,status", [(0, 400), (1, 400), (1, 422), (2, 400), (2, 422)])
async def test_all_format_modes_keep_complete_schema_and_do_not_mutate_messages(fallbacks, status):
    requests = []
    messages, schema = copy.deepcopy(MESSAGES), copy.deepcopy(SCHEMA)
    output = {"memories": [], "reason": "No durable preference"}

    def handler(request):
        requests.append(json.loads(request.content))
        return (httpx.Response(status, json={"error": "unsupported format"})
                if len(requests) <= fallbacks else completion(json.dumps(output)))

    provider = await provider_for(handler)
    try:
        assert await provider.complete_structured(
            messages, schema, temperature=0, max_tokens=1800,
        ) == output
    finally:
        await provider.close()
    assert len(requests) == fallbacks + 1
    assert requests[0]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert requests[0]["messages"] == MESSAGES
    for index, body in enumerate(requests):
        assert body["temperature"] == 0 and body["max_tokens"] == 1800
        assert body["model"] == "offline-model"
        if index:
            assert body["messages"][:-1] == MESSAGES
            hint = body["messages"][-1]["content"]
            assert json.loads(hint[hint.index("{"):]) == SCHEMA
            assert "$defs" in hint and "additionalProperties" in hint
        if index == 1:
            assert body["response_format"] == {"type": "json_object"}
        if index == 2:
            assert "response_format" not in body
            assert body["messages"] == requests[1]["messages"]
    assert messages == MESSAGES and schema == SCHEMA


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 500])
@pytest.mark.parametrize("fallbacks", [0, 1, 2])
async def test_non_format_errors_never_trigger_another_attempt(status, fallbacks):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            400 if len(requests) <= fallbacks else status,
            json={"error": "SECRET-provider-error"},
        )

    provider = await provider_for(handler)
    try:
        with pytest.raises(LLMHTTPError) as error:
            await provider.complete_structured(MESSAGES, SCHEMA)
        assert error.value.status_code == status
        assert "SECRET" not in str(error.value)
        assert len(requests) == fallbacks + 1
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_format_rejections_stop_after_three_attempts():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(422, json={"error": "unsupported"})

    provider = await provider_for(handler)
    try:
        with pytest.raises(LLMHTTPError):
            await provider.complete_structured(MESSAGES, SCHEMA)
        assert len(requests) == 3
    finally:
        await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("content,error_type", [
    ("SECRET not JSON", LLMStructuredOutputError),
    ('{"memories": SECRET}', LLMStructuredOutputError),
    ("null", LLMResponseError),
    ('"SECRET"', LLMResponseError),
])
async def test_invalid_json_has_a_safe_typed_error_without_retries(content, error_type):
    requests = []

    def handler(request):
        requests.append(request)
        return completion(content)

    provider = await provider_for(handler)
    try:
        with pytest.raises(error_type) as error:
            await provider.complete_structured(MESSAGES, SCHEMA)
        assert "SECRET" not in str(error.value)
        assert not hasattr(error.value, "doc")
        assert len(requests) == 1
    finally:
        await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    "<!doctype html>SECRET", "[]", "{}", '{"choices":{"SECRET":0}}',
    '{"choices":[{"message":["SECRET"]}]}',
    '{"choices":[{"message":{"content":""}}]}',
])
async def test_malformed_response_envelope_has_a_safe_typed_error(body):
    provider = await provider_for(lambda request: httpx.Response(200, text=body))
    try:
        with pytest.raises(LLMResponseError) as error:
            await provider.complete_structured(MESSAGES, SCHEMA)
        assert "SECRET" not in str(error.value)
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_existing_array_contract_remains_compatible():
    provider = await provider_for(lambda request: completion('[{"id":"existing-curation-item"}]'))
    try:
        assert await provider.complete_structured(MESSAGES, {"type": "array"}) == {
            "items": [{"id": "existing-curation-item"}],
        }
    finally:
        await provider.close()
