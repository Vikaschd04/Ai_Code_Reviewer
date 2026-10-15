"""Provider drivers against an in-process HTTP transport (test double; no network)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from crp_analysis.ai.config import configured, default_connection, resolve
from crp_analysis.ai.models import (
    Message,
    ModelRequest,
    ProviderError,
    ProviderErrorCode,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)
from crp_analysis.ai.providers import AnthropicClient, ClientOptions, OpenAICompatibleClient
from crp_core.config import Settings

KEY = "sk-test-secret-value-123"
TOOL = ToolSpec("submit_answer", "Submit", {"type": "object", "properties": {}})
REQUEST = ModelRequest(
    system="You review code.",
    messages=(
        Message("user", (TextBlock("Question?"),)),
        Message("assistant", (ToolUseBlock("t1", "read_file", {"path": "a.py"}),)),
        Message("user", (ToolResultBlock("t1", "line 1"),)),
    ),
    tools=(TOOL,),
    tool_choice="auto",
    max_output_tokens=512,
)

Handler = Callable[[httpx.Request], httpx.Response]


def _sequence(*responses: httpx.Response | Exception) -> tuple[Handler, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return handler, seen


class _Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _anthropic(
    handler: Handler, sleeps: _Sleeps | None = None, retries: int = 2
) -> AnthropicClient:
    return AnthropicClient(
        api_key=KEY,
        model="claude-test",
        options=ClientOptions(timeout_seconds=5, max_retries=retries),
        transport=httpx.MockTransport(handler),
        sleep=sleeps or _Sleeps(),
    )


def _openai(handler: Handler, **kwargs: str) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        api_key=KEY,
        model="gpt-test",
        base_url="https://llm.example.test/v1",
        options=ClientOptions(timeout_seconds=5, max_retries=2),
        transport=httpx.MockTransport(handler),
        sleep=_Sleeps(),
        **kwargs,
    )


ANTHROPIC_OK = {
    "id": "msg_1",
    "model": "claude-test",
    "content": [
        {"type": "text", "text": "Looking."},
        {"type": "tool_use", "id": "toolu_1", "name": "submit_answer", "input": {"answer": "x"}},
    ],
    "stop_reason": "tool_use",
    "usage": {
        "input_tokens": 120,
        "output_tokens": 30,
        "cache_read_input_tokens": 5,
        "cache_creation_input_tokens": 7,
    },
}


async def test_anthropic_success_maps_request_and_response() -> None:
    handler, seen = _sequence(httpx.Response(200, json=ANTHROPIC_OK, headers={"request-id": "r1"}))
    response = await _anthropic(handler).complete(REQUEST)
    sent = seen[0]
    assert str(sent.url) == "https://api.anthropic.com/v1/messages"
    assert sent.headers["x-api-key"] == KEY and sent.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(sent.content)
    assert body["system"] == "You review code." and body["max_tokens"] == 512
    assert "temperature" not in body
    assert body["tool_choice"] == {"type": "auto"}
    assert body["messages"][2]["content"][0]["type"] == "tool_result"
    assert response.stop_reason is StopReason.TOOL_USE and response.request_id == "r1"
    assert response.tool_calls[0].input == {"answer": "x"} and response.text == "Looking."
    assert (response.usage.input_tokens, response.usage.output_tokens) == (120, 30)
    assert (response.usage.cache_read_tokens, response.usage.cache_write_tokens) == (5, 7)


async def test_openai_compatible_success_maps_request_and_response() -> None:
    ok = {
        "model": "gpt-test",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "submit_answer", "arguments": '{"answer": "y"}'},
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 90,
            "completion_tokens": 12,
            "prompt_tokens_details": {"cached_tokens": 4},
        },
    }
    handler, seen = _sequence(httpx.Response(200, json=ok))
    response = await _openai(handler).complete(REQUEST)
    sent = seen[0]
    assert str(sent.url) == "https://llm.example.test/v1/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(sent.content)
    assert body["messages"][0] == {"role": "system", "content": "You review code."}
    assert body["messages"][2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "t1", "content": "line 1"}
    assert body["max_completion_tokens"] == 512 and body["tool_choice"] == "auto"
    assert response.tool_calls[0].input == {"answer": "y"}
    assert response.stop_reason is StopReason.TOOL_USE
    assert (response.usage.input_tokens, response.usage.cache_read_tokens) == (90, 4)


async def test_openai_azure_style_auth_and_legacy_max_tokens() -> None:
    ok = {"choices": [{"finish_reason": "stop", "message": {"content": "hi"}}]}
    handler, seen = _sequence(httpx.Response(200, json=ok))
    response = await _openai(
        handler, auth_header="api-key", max_tokens_field="max_tokens"
    ).complete(REQUEST)
    assert seen[0].headers["api-key"] == KEY and "authorization" not in seen[0].headers
    assert json.loads(seen[0].content)["max_tokens"] == 512
    assert response.usage.reported is False and response.text == "hi"


async def test_rate_limit_is_retried_after_the_advised_delay() -> None:
    sleeps = _Sleeps()
    handler, seen = _sequence(
        httpx.Response(
            429,
            json={"error": {"type": "rate_limit_error", "message": "slow down"}},
            headers={"retry-after": "3"},
        ),
        httpx.Response(200, json=ANTHROPIC_OK),
    )
    response = await _anthropic(handler, sleeps).complete(REQUEST)
    assert len(seen) == 2 and sleeps.delays == [3.0] and response.attempts == 2


async def test_rate_limit_exhaustion_and_overload_raise_typed_errors() -> None:
    limited = httpx.Response(429, json={"error": {"type": "rate_limit_error", "message": "x"}})
    handler, seen = _sequence(limited, limited, limited)
    with pytest.raises(ProviderError) as caught:
        await _anthropic(handler).complete(REQUEST)
    assert caught.value.code is ProviderErrorCode.RATE_LIMITED and caught.value.attempts == 3
    assert len(seen) == 3

    handler, _ = _sequence(httpx.Response(529, json={"error": {"type": "overloaded_error"}}))
    with pytest.raises(ProviderError) as overload:
        await _anthropic(handler, retries=0).complete(REQUEST)
    assert overload.value.code is ProviderErrorCode.OVERLOADED and overload.value.retryable


async def test_timeouts_are_retried_then_reported() -> None:
    sleeps = _Sleeps()
    boom = httpx.ReadTimeout("slow")
    handler, seen = _sequence(boom, boom, boom)
    with pytest.raises(ProviderError) as caught:
        await _anthropic(handler, sleeps).complete(REQUEST)
    assert caught.value.code is ProviderErrorCode.TIMEOUT and len(seen) == 3
    assert len(sleeps.delays) == 2 and sleeps.delays[1] > sleeps.delays[0] >= 1.0


async def test_authentication_errors_are_not_retried_and_hide_the_key() -> None:
    handler, seen = _sequence(
        httpx.Response(
            401, json={"error": {"type": "authentication_error", "message": "invalid x-api-key"}}
        )
    )
    with pytest.raises(ProviderError) as caught:
        await _anthropic(handler).complete(REQUEST)
    assert caught.value.code is ProviderErrorCode.AUTHENTICATION and len(seen) == 1
    assert KEY not in str(caught.value)


async def test_malformed_responses_are_reported_without_retry() -> None:
    handler, seen = _sequence(httpx.Response(200, content=b"<html>proxy error</html>"))
    with pytest.raises(ProviderError) as caught:
        await _anthropic(handler).complete(REQUEST)
    assert caught.value.code is ProviderErrorCode.MALFORMED_RESPONSE and len(seen) == 1

    handler, _ = _sequence(httpx.Response(200, json={"content": [{"type": "tool_use", "id": "t"}]}))
    with pytest.raises(ProviderError):
        await _anthropic(handler).complete(REQUEST)

    bad_args = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [{"id": "c", "function": {"name": "x", "arguments": "{not json"}}]
                },
            }
        ]
    }
    handler, _ = _sequence(httpx.Response(200, json=bad_args))
    with pytest.raises(ProviderError) as openai_bad:
        await _openai(handler).complete(REQUEST)
    assert openai_bad.value.code is ProviderErrorCode.MALFORMED_RESPONSE


def test_setup_explains_missing_configuration(
    make_settings: Callable[..., Settings], tmp_path: Path
) -> None:
    none = resolve(make_settings())
    assert not none.available and none.reason and "CRP_AI_PROVIDER" in (none.admin_hint or "")

    no_key = resolve(make_settings(ai_provider="anthropic"))
    assert not no_key.available and "CRP_AI_API_KEY" in (no_key.admin_hint or "")
    assert no_key.model == "claude-opus-5-5"

    no_model = resolve(make_settings(ai_provider="openai_compatible", ai_api_key=KEY))
    assert not no_model.available and "CRP_AI_MODEL" in (no_model.admin_hint or "")

    key_file = tmp_path / "ai-key"
    key_file.write_text(KEY + "\n")
    key_file.chmod(0o600)
    ready = resolve(make_settings(ai_provider="anthropic", ai_api_key_file=key_file))
    assert ready.available and ready.reason is None
    assert KEY not in repr(ready)  # the key is never part of the printable setup

    def openai(url: str) -> bool:
        return resolve(
            make_settings(
                ai_provider="openai_compatible", ai_model="m", ai_api_key=KEY, ai_base_url=url
            )
        ).available

    assert openai("https://models.example.com/v1") and openai("http://127.0.0.1:8080/v1")
    plain = resolve(
        make_settings(
            ai_provider="openai_compatible",
            ai_model="m",
            ai_api_key=KEY,
            ai_base_url="http://models.example.com/v1",
        )
    )
    assert not plain.available and "https" in (plain.admin_hint or "")  # key would travel in clear

    priced = resolve(
        make_settings(ai_price_input_per_mtok_usd=3.0, ai_price_output_per_mtok_usd=15.0)
    )
    assert priced.prices.cost(1_000_000, 100_000) == pytest.approx(4.5)
    assert none.prices.cost(10, 10) is None  # unknown prices are never invented


def test_two_connections_resolve_with_their_own_label_note_budget_and_pacing(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(
        ai_provider="anthropic",
        ai_api_key=KEY,
        ai_monthly_token_limit=1_000_000,
        ai_alt_provider="openai_compatible",
        ai_alt_model="glm-5.3:free",
        ai_alt_base_url="https://api.unorouter.com/v1",
        ai_alt_api_key="sk-alt-secret-value-456",
        ai_alt_label="Free test model",
        ai_alt_data_note="Upstream providers may keep prompts; use only shareable code.",
        ai_alt_min_interval_seconds=61,
        ai_alt_run_timeout_seconds=1800,
        ai_alt_monthly_token_limit=0,
        ai_alt_price_input_per_mtok_usd=0,
        ai_alt_price_output_per_mtok_usd=0,
    )
    primary, alt = resolve(settings), resolve(settings, "alt")
    assert (primary.connection, primary.label, primary.free) == (
        "primary",
        "claude-opus-5-5 (Anthropic)",
        False,
    )
    assert primary.limits.timeout_seconds == 600 and primary.monthly_token_limit == 1_000_000
    assert alt.available and alt.connection == "alt" and alt.label == "Free test model"
    assert alt.model == "glm-5.3:free" and alt.base_url == "https://api.unorouter.com/v1"
    assert alt.data_note and alt.free and alt.prices.cost(1000, 1000) == 0
    assert alt.limits.timeout_seconds == 1800 and alt.monthly_token_limit == 0
    assert "sk-alt-secret-value-456" not in repr(alt)
    assert [c.connection for c in configured(settings)] == ["primary", "alt"]
    assert default_connection(settings) == "primary"

    missing = resolve(make_settings(ai_alt_provider="openai_compatible", ai_alt_model="m"), "alt")
    assert not missing.available and "CRP_AI_ALT_API_KEY" in (missing.admin_hint or "")
    clear = resolve(
        make_settings(
            ai_alt_provider="openai_compatible",
            ai_alt_model="m",
            ai_alt_api_key=KEY,
            ai_alt_base_url="http://models.example.com/v1",
        ),
        "alt",
    )
    assert not clear.available and "CRP_AI_ALT_BASE_URL" in (clear.admin_hint or "")
    # Only the alternate is usable: new projects start with it even if the primary is preferred.
    only_alt = make_settings(
        ai_alt_provider="openai_compatible", ai_alt_model="m", ai_alt_api_key=KEY
    )
    assert default_connection(only_alt) == "alt"
    assert [c.connection for c in configured(make_settings())] == []


OPENAI_OK = {"choices": [{"finish_reason": "stop", "message": {"content": "hi"}}]}


async def test_free_tier_pacing_spaces_requests_and_waits_out_rate_limits() -> None:
    sleeps = _Sleeps()
    handler, seen = _sequence(
        httpx.Response(200, json=OPENAI_OK),
        httpx.Response(429, headers={"retry-after": "70"}, json={"error": {"message": "slow"}}),
        httpx.Response(200, json=OPENAI_OK),
    )
    client = OpenAICompatibleClient(
        api_key=KEY,
        model="glm-test:free",
        base_url="https://free.example.test/v1",
        options=ClientOptions(
            timeout_seconds=5, max_retries=2, min_interval_seconds=60, max_retry_after_seconds=90
        ),
        transport=httpx.MockTransport(handler),
        sleep=sleeps,
    )
    await client.complete(REQUEST)
    await client.complete(REQUEST)
    assert len(seen) == 3
    # The second request waits for the interval; the rate limit's retry-after (70 s) is honoured
    # because it is under this connection's cap, and the retry is paced as well.
    assert len(sleeps.delays) == 3
    assert 59 < sleeps.delays[0] <= 60 and sleeps.delays[1] == 70.0 and sleeps.delays[2] <= 60


async def test_free_tier_pacing_counts_from_the_end_of_a_slow_answer() -> None:
    """A free tier's window can start when the previous (slow) answer finished."""
    sleeps = _Sleeps()

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)
        return httpx.Response(200, json=OPENAI_OK)

    client = OpenAICompatibleClient(
        api_key=KEY,
        model="slow-test:free",
        base_url="https://slow.example.test/v1",
        options=ClientOptions(timeout_seconds=5, max_retries=0, min_interval_seconds=0.5),
        transport=httpx.MockTransport(slow),
        sleep=sleeps,
    )
    await client.complete(REQUEST)
    await client.complete(REQUEST)
    assert len(sleeps.delays) == 1 and sleeps.delays[0] > 0.45  # not 0.2 (from the start)
