"""API-compatible LLM provider implementation.

Sends API-compatible chat completion requests via httpx.
Requires an explicitly configured endpoint, model, and API key.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from c2video.config.schema import LLMConfig
from c2video.llm.base import LLMProvider
from c2video.usage import record_usage
from c2video.util import parse_json_payload


class LLMHTTPError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        # Responses can contain echoed prompts and secrets. Do not persist their bodies.
        super().__init__(f"LLM HTTP {status_code}")


class LLMResponseError(RuntimeError):
    """The HTTP response did not contain a usable chat completion."""


class LLMStructuredOutputError(ValueError):
    """The completion text could not be parsed as JSON."""


class APICompatibleLLMProvider(LLMProvider):
    """LLM provider that speaks an API-compatible chat completion protocol."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        if not all(str(value).strip() for value in
                   (config.api_base_url, config.model, config.api_key)):
            raise ValueError("API 模型需要配置 api_base_url、model 和 api_key；无密钥请用 llm.provider=local。")
        base = config.api_base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=base,
            headers={"Content-Type": "application/json"},
            timeout=config.timeout_seconds,
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.api_key.strip()}",
                "Content-Type": "application/json"}

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        data = None
        http_status = None
        status = "failed"
        error_type = None
        try:
            response = await self._client.post(
                "/chat/completions", json=body, headers=self._headers()
            )
            http_status = response.status_code
            try:
                parsed = response.json()
                data = parsed if isinstance(parsed, dict) else None
            except ValueError:
                data = None
            if response.status_code >= 400:
                raise LLMHTTPError(response.status_code)
            if data is None:
                raise LLMResponseError("LLM response was not a JSON object")
            self._content(data)
            status = "succeeded"
            return data
        except asyncio.CancelledError:
            status = "canceled"
            error_type = "CancelledError"
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            try:
                record_usage(
                    self.config, data=data, status=status, http_status=http_status,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    error_type=error_type,
                )
            except Exception as exc:
                # Observability must not turn a successful paid call into a retry.
                logging.getLogger(__name__).warning(
                    "Model usage could not be recorded (%s)", type(exc).__name__,
                )

    @staticmethod
    def _content(data: dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMResponseError("LLM response missing choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise LLMResponseError("LLM response missing message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("LLM response missing text content")
        return content

    async def complete(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> str:
        """Send a chat completion request and return the response text."""
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "temperature": kwargs.get("temperature", self.config.temperature),
        }
        return self._content(await self._post(body))

    async def complete_structured(
        self,
        messages: list[dict[str, str]],
        output_schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a chat completion and parse JSON. Falls back if schema mode fails."""
        base_body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "temperature": kwargs.get("temperature", self.config.temperature),
        }
        schema_body = {
            **base_body,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": output_schema, "strict": False},
            },
        }
        try:
            content = self._content(await self._post(schema_body))
        except LLMHTTPError as exc:
            if exc.status_code not in {400, 422}:
                raise
            # JSON mode guarantees syntax, not our contract. Preserve the full
            # schema (including nested definitions) in BOTH compatibility modes.
            # Copy messages so another call cannot inherit this fallback hint.
            hint = {
                "role": "user",
                "content": (
                    "Respond with ONLY JSON matching this JSON Schema. "
                    "Do not add fields or explanatory text.\n"
                    + json.dumps(output_schema, ensure_ascii=False)
                ),
            }
            fallback_messages = [*messages, hint]
            json_body = {
                **base_body,
                "messages": fallback_messages,
                "response_format": {"type": "json_object"},
            }
            try:
                content = self._content(await self._post(json_body))
            except LLMHTTPError as exc:
                if exc.status_code not in {400, 422}:
                    raise
                content = await self.complete(fallback_messages, **kwargs)
        try:
            parsed = parse_json_payload(content)
        except ValueError:
            # JSONDecodeError retains the full response in .doc. Do not expose
            # it to callers that persist diagnostic information.
            raise LLMStructuredOutputError("LLM structured output was not JSON") from None
        if not isinstance(parsed, (dict, list)):
            raise LLMResponseError("LLM structured output was not a JSON object or array")
        if isinstance(parsed, list):
            return {"items": parsed}
        return parsed

    async def close(self) -> None:
        """Release the underlying HTTP client."""
        await self._client.aclose()
