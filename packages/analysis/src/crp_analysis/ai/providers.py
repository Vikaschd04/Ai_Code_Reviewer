"""Model provider drivers: Anthropic Messages API and OpenAI-compatible chat completions.

Both drivers map the neutral contract in ``models`` to their wire format, enforce a request
timeout, retry only retryable failures (rate limits, overload, server errors, timeouts, network)
a bounded number of times with jittered exponential backoff that honours ``retry-after``, and
return provider-reported usage. Error messages never include the API key or the request body.
Tool calls carry structured output on both providers (a final ``submit_*`` tool), so no provider
specific JSON mode is needed. Temperature is not sent (newer Claude models reject non-default).
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from crp_analysis.ai.models import (
    Block,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorCode,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)

ANTHROPIC_VERSION = "2023-06-01"
_MAX_RETRY_AFTER = 30.0
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})

Sleep = Callable[[float], Awaitable[None]]


class ModelClient(Protocol):
    provider: str
    model: str

    async def complete(self, request: ModelRequest) -> ModelResponse: ...


@dataclass(frozen=True, slots=True)
class ClientOptions:
    timeout_seconds: float = 120.0
    max_retries: int = 2
    base_backoff_seconds: float = 1.0


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, min(float(value), _MAX_RETRY_AFTER))
    except ValueError:
        return None


def _status_error(response: httpx.Response, request_id: str | None) -> ProviderError:
    status = response.status_code
    detail = ""
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            kind = error.get("type") or error.get("code") or ""
            message = str(error.get("message") or "")[:300]
            detail = f"{kind}: {message}".strip(": ")
    except ValueError:
        detail = ""
    if status == 401:
        code = ProviderErrorCode.AUTHENTICATION
    elif status == 403:
        code = ProviderErrorCode.PERMISSION
    elif status == 429:
        code = ProviderErrorCode.RATE_LIMITED
    elif status == 529:
        code = ProviderErrorCode.OVERLOADED
    elif status >= 500:
        code = ProviderErrorCode.SERVER_ERROR
    else:
        code = ProviderErrorCode.INVALID_REQUEST
    message = f"provider answered HTTP {status}" + (f" ({detail})" if detail else "")
    return ProviderError(
        code,
        message,
        status=status,
        retryable=status in _RETRYABLE_STATUS,
        retry_after=_retry_after(response),
        request_id=request_id,
    )


class _HttpDriver:
    """Shared HTTP handling: timeout, bounded retries with backoff, error mapping."""

    def __init__(
        self,
        *,
        base_url: str,
        headers: dict[str, str],
        options: ClientOptions,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = headers
        self._options = options
        self._transport = transport
        self._sleep = sleep

    async def post(
        self, path: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None, int, int]:
        """POST JSON; returns (body, request_id, latency_ms, attempts) or raises ProviderError."""
        attempts = 0
        last: ProviderError | None = None
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=httpx.Timeout(self._options.timeout_seconds),
            headers=self._headers,
        ) as client:
            while attempts <= self._options.max_retries:
                attempts += 1
                started = time.monotonic()
                try:
                    response = await client.post(f"{self._base_url}{path}", json=payload)
                except httpx.TimeoutException:
                    last = ProviderError(
                        ProviderErrorCode.TIMEOUT,
                        f"provider did not answer within {self._options.timeout_seconds:g}s",
                        retryable=True,
                    )
                except httpx.TransportError as exc:
                    last = ProviderError(
                        ProviderErrorCode.NETWORK,
                        f"could not reach the provider ({type(exc).__name__})",
                        retryable=True,
                    )
                else:
                    latency = int((time.monotonic() - started) * 1000)
                    request_id = response.headers.get("request-id") or response.headers.get(
                        "x-request-id"
                    )
                    if response.is_success:
                        try:
                            body = response.json()
                        except ValueError as exc:
                            raise ProviderError(
                                ProviderErrorCode.MALFORMED_RESPONSE,
                                "provider returned a non-JSON body",
                                request_id=request_id,
                                attempts=attempts,
                            ) from exc
                        if not isinstance(body, dict):
                            raise ProviderError(
                                ProviderErrorCode.MALFORMED_RESPONSE,
                                "provider returned JSON that is not an object",
                                request_id=request_id,
                                attempts=attempts,
                            )
                        return body, request_id, latency, attempts
                    last = _status_error(response, request_id)
                last.attempts = attempts
                if not last.retryable or attempts > self._options.max_retries:
                    raise last
                delay = last.retry_after
                if delay is None:
                    delay = self._options.base_backoff_seconds * (2 ** (attempts - 1))
                    delay += random.uniform(0, delay / 2)  # noqa: S311 - jitter, not security
                await self._sleep(delay)
        raise last or ProviderError(ProviderErrorCode.NETWORK, "provider call was not attempted")


def _require(body: dict[str, Any], key: str, kind: type) -> Any:
    value = body.get(key)
    if not isinstance(value, kind):
        raise ProviderError(
            ProviderErrorCode.MALFORMED_RESPONSE, f"provider response lacks a valid '{key}'"
        )
    return value


def _int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


# ---- Anthropic ------------------------------------------------------------------------------


def _anthropic_block(block: Block) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return {
        "type": "tool_result",
        "tool_use_id": block.tool_use_id,
        "content": block.content,
        "is_error": block.is_error,
    }


class AnthropicClient:
    provider = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.anthropic.com",
        options: ClientOptions | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.model = model
        self._http = _HttpDriver(
            base_url=base_url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            options=options or ClientOptions(),
            transport=transport,
            sleep=sleep,
        )

    def payload(self, request: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_output_tokens,
            "system": request.system,
            "messages": [
                {"role": m.role, "content": [_anthropic_block(b) for b in m.content]}
                for m in request.messages
            ],
        }
        if request.tools:
            payload["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in request.tools
            ]
            if request.tool_choice == "auto":
                payload["tool_choice"] = {"type": "auto"}
            elif request.tool_choice == "any":
                payload["tool_choice"] = {"type": "any"}
            else:
                payload["tool_choice"] = {"type": "tool", "name": request.tool_choice}
        return payload

    async def complete(self, request: ModelRequest) -> ModelResponse:
        body, request_id, latency, attempts = await self._http.post(
            "/v1/messages", self.payload(request)
        )
        blocks = _require(body, "content", list)
        texts: list[str] = []
        calls: list[ToolUseBlock] = []
        content: list[Block] = []
        for block in blocks:
            if not isinstance(block, dict):
                raise ProviderError(
                    ProviderErrorCode.MALFORMED_RESPONSE, "content block is not an object"
                )
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
                content.append(TextBlock(block["text"]))
            elif block.get("type") == "tool_use":
                if not (
                    isinstance(block.get("id"), str)
                    and isinstance(block.get("name"), str)
                    and isinstance(block.get("input"), dict)
                ):
                    raise ProviderError(
                        ProviderErrorCode.MALFORMED_RESPONSE, "tool_use block is incomplete"
                    )
                call = ToolUseBlock(block["id"], block["name"], block["input"])
                calls.append(call)
                content.append(call)
        stop = {
            "end_turn": StopReason.END_TURN,
            "tool_use": StopReason.TOOL_USE,
            "max_tokens": StopReason.MAX_TOKENS,
        }.get(str(body.get("stop_reason")), StopReason.OTHER)
        raw_usage = body.get("usage")
        usage = (
            Usage(
                _int(raw_usage.get("input_tokens")),
                _int(raw_usage.get("output_tokens")),
                _int(raw_usage.get("cache_read_input_tokens")),
                _int(raw_usage.get("cache_creation_input_tokens")),
            )
            if isinstance(raw_usage, dict)
            else Usage(reported=False)
        )
        return ModelResponse(
            text="\n".join(texts),
            tool_calls=tuple(calls),
            stop_reason=stop,
            usage=usage,
            model=str(body.get("model") or self.model),
            request_id=request_id,
            latency_ms=latency,
            attempts=attempts,
            content=tuple(content),
        )


# ---- OpenAI-compatible ---------------------------------------------------------------------


def _openai_messages(request: ModelRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": request.system}]
    for message in request.messages:
        texts = [b.text for b in message.content if isinstance(b, TextBlock)]
        if message.role == "assistant":
            calls = [b for b in message.content if isinstance(b, ToolUseBlock)]
            entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts) or None}
            if calls:
                entry["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.input)},
                    }
                    for c in calls
                ]
            messages.append(entry)
            continue
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                content = f"ERROR: {block.content}" if block.is_error else block.content
                messages.append(
                    {"role": "tool", "tool_call_id": block.tool_use_id, "content": content}
                )
        if texts:
            messages.append({"role": "user", "content": "\n".join(texts)})
    return messages


class OpenAICompatibleClient:
    provider = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        auth_header: str = "bearer",
        max_tokens_field: str = "max_completion_tokens",
        options: ClientOptions | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.model = model
        self._max_tokens_field = max_tokens_field
        headers = {"content-type": "application/json"}
        if auth_header == "api-key":
            headers["api-key"] = api_key
        else:
            headers["authorization"] = f"Bearer {api_key}"
        self._http = _HttpDriver(
            base_url=base_url,
            headers=headers,
            options=options or ClientOptions(),
            transport=transport,
            sleep=sleep,
        )

    def payload(self, request: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _openai_messages(request),
            self._max_tokens_field: request.max_output_tokens,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
            if request.tool_choice == "auto":
                payload["tool_choice"] = "auto"
            elif request.tool_choice == "any":
                payload["tool_choice"] = "required"
            else:
                payload["tool_choice"] = {
                    "type": "function",
                    "function": {"name": request.tool_choice},
                }
        return payload

    async def complete(self, request: ModelRequest) -> ModelResponse:
        body, request_id, latency, attempts = await self._http.post(
            "/chat/completions", self.payload(request)
        )
        choices = _require(body, "choices", list)
        if not choices or not isinstance(choices[0], dict):
            raise ProviderError(
                ProviderErrorCode.MALFORMED_RESPONSE, "provider returned no choices"
            )
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ProviderError(ProviderErrorCode.MALFORMED_RESPONSE, "choice has no message")
        raw_content = message.get("content")
        text: str = raw_content if isinstance(raw_content, str) else ""
        refusal = message.get("refusal")
        if isinstance(refusal, str) and refusal:
            text = f"{text}\n{refusal}".strip()
        calls: list[ToolUseBlock] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") if isinstance(raw, dict) else None
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                raise ProviderError(ProviderErrorCode.MALFORMED_RESPONSE, "tool call is incomplete")
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except ValueError as exc:
                raise ProviderError(
                    ProviderErrorCode.MALFORMED_RESPONSE, "tool call arguments are not JSON"
                ) from exc
            if not isinstance(arguments, dict):
                raise ProviderError(
                    ProviderErrorCode.MALFORMED_RESPONSE, "tool call arguments are not an object"
                )
            calls.append(
                ToolUseBlock(
                    str(raw.get("id") or f"call_{len(calls)}"), function["name"], arguments
                )
            )
        stop = {
            "stop": StopReason.END_TURN,
            "tool_calls": StopReason.TOOL_USE,
            "function_call": StopReason.TOOL_USE,
            "length": StopReason.MAX_TOKENS,
        }.get(str(choices[0].get("finish_reason")), StopReason.OTHER)
        raw_usage = body.get("usage")
        if isinstance(raw_usage, dict):
            details = raw_usage.get("prompt_tokens_details")
            cached = _int(details.get("cached_tokens")) if isinstance(details, dict) else 0
            usage = Usage(
                _int(raw_usage.get("prompt_tokens")),
                _int(raw_usage.get("completion_tokens")),
                cached,
                0,
            )
        else:
            usage = Usage(reported=False)
        content: list[Block] = [TextBlock(text)] if text else []
        content.extend(calls)
        return ModelResponse(
            text=text,
            tool_calls=tuple(calls),
            stop_reason=stop,
            usage=usage,
            model=str(body.get("model") or self.model),
            request_id=request_id,
            latency_ms=latency,
            attempts=attempts,
            content=tuple(content),
        )
