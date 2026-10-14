"""Projects' own AI models and keys (ADR 0026): encryption bound to the project, the model
catalog, key checks that send no code, the run configuration, and the Claude adapter details that
current models need (no forced tool choice; thinking blocks sent back unchanged)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from crp_analysis.ai import catalog
from crp_analysis.ai.config import project_key_context, project_setup
from crp_analysis.ai.keytest import check_anthropic, check_openai_compatible
from crp_analysis.ai.models import (
    Message,
    ModelRequest,
    ProviderBlock,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)
from crp_analysis.ai.providers import AnthropicClient, ClientOptions, OpenAICompatibleClient
from crp_core.config import AiProvider, Settings
from crp_core.secret_box import SecretBox, SecretBoxError, secret_box

USER_KEY = "sk-ant-user-key-0123456789"
SERVER_SECRET = "s" * 40


def test_keys_are_encrypted_and_bound_to_their_project_and_provider(
    make_settings: Callable[..., Settings],
) -> None:
    box = SecretBox(SERVER_SECRET)
    context = project_key_context("p1", "anthropic")
    sealed = box.encrypt(USER_KEY, context)
    assert sealed.startswith("v1.") and USER_KEY not in sealed
    assert box.encrypt(USER_KEY, context) != sealed  # a fresh nonce every time
    assert box.decrypt(sealed, context) == USER_KEY
    for wrong in (project_key_context("p2", "anthropic"), project_key_context("p1", "unorouter")):
        with pytest.raises(SecretBoxError):
            box.decrypt(sealed, wrong)  # copied to another project or provider: unreadable
    with pytest.raises(SecretBoxError):
        SecretBox("t" * 40).decrypt(sealed, context)  # another server secret
    with pytest.raises(SecretBoxError):
        box.decrypt(sealed[:-4] + "AAAA", context)  # tampered
    assert secret_box(make_settings()) is None  # not set up: own keys cannot be saved
    assert secret_box(make_settings(secrets_key="short")) is None
    assert secret_box(make_settings(secrets_key=SERVER_SECRET)) is not None


def test_catalog_offers_current_claude_models_and_free_tool_capable_unorouter_models() -> None:
    ids = [m.id for m in catalog.ANTHROPIC_MODELS]
    assert ids[0] == "claude-opus-5-5" and "claude-fable-5-1" in ids and "claude-opus-4-8" in ids
    assert not any("mythos" in i for i in ids)
    assert catalog.find_model("anthropic", "claude-opus-5-5").price_input == 4.0  # type: ignore[union-attr]

    def item(name: str, **meta: object) -> dict[str, object]:
        base = {"mode": "chat", "supportsTools": True, "outputModalities": ["text"]}
        return {
            "model_name": name,
            "is_free": name.endswith(":free"),
            "online": True,
            "supported_endpoint_types": ["openai"],
            "metadata": json.dumps(base | meta),
        }

    data = {
        "data": [
            item("glm-5.3:free", releaseTs=2, maxOutputTokens=943718),
            item("deepseek-v4.1-flash:free", releaseTs=3, maxOutputTokens=4000),
            item("paid-model", releaseTs=9),
            item("no-tools:free", supportsTools=False),
            item("image-maker:free", mode="image"),
            {**item("offline:free"), "online": False},
            {**item("embedding:free"), "supported_endpoint_types": ["embedding"]},
            {"model_name": "broken:free", "is_free": True, "online": True, "metadata": "{"},
        ]
    }
    models = catalog.unorouter_models(data)
    assert [m.id for m in models] == ["deepseek-v4.1-flash:free", "glm-5.3:free"]
    assert [m.max_output_tokens for m in models] == [4000, catalog.FREE_OUTPUT_CAP]
    assert all(m.free and m.price_input == 0 for m in models)


async def test_live_unorouter_list_falls_back_when_unreadable() -> None:
    catalog._UNOROUTER_CACHE.models = []
    down = httpx.MockTransport(lambda request: httpx.Response(503))
    models, live = await catalog.live_unorouter(down, now=1.0)
    assert not live and [m.id for m in models] == [m.id for m in catalog.UNOROUTER_FALLBACK]


async def test_anthropic_key_check_reads_the_model_and_maps_errors() -> None:
    seen: list[httpx.Request] = []

    def answer(status: int) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(status, json={"error": {"message": f"echo {USER_KEY}"}})

        return httpx.MockTransport(handle)

    ok = await check_anthropic(
        USER_KEY, "claude-opus-5-5", "https://api.anthropic.com", answer(200)
    )
    assert ok.ok
    request = seen[-1]
    assert request.method == "GET" and request.url.path == "/v1/models/claude-opus-5-5"
    assert request.headers["x-api-key"] == USER_KEY and not request.content  # no code sent
    for status, words in ((401, "did not accept"), (404, "does not offer"), (429, "Wait a minute")):
        result = await check_anthropic(
            USER_KEY, "claude-opus-5-5", "https://x.test", answer(status)
        )
        assert not result.ok and words in result.message and USER_KEY not in result.message


async def test_unorouter_key_check_needs_a_tool_call() -> None:
    bodies: list[dict[str, object]] = []

    def reply(tool_calls: list[dict[str, object]] | None) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            message = {"role": "assistant", "content": "ok", "tool_calls": tool_calls}
            return httpx.Response(200, json={"choices": [{"message": message}]})

        return httpx.MockTransport(handle)

    call = [{"id": "c1", "type": "function", "function": {"name": "report_connection"}}]
    good = await check_openai_compatible(
        USER_KEY, "glm-5.3:free", "https://api.unorouter.com/v1", "UnoRouter", reply(call)
    )
    assert good.ok and bodies[-1]["model"] == "glm-5.3:free"
    assert "report_connection" in json.dumps(bodies[-1]["tools"])
    lazy = await check_openai_compatible(
        USER_KEY, "glm-5.3:free", "https://api.unorouter.com/v1", "UnoRouter", reply(None)
    )
    assert not lazy.ok and "tool calls" in lazy.message


def test_project_setup_uses_the_catalog_and_no_server_budget(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(ai_monthly_token_limit=1000)
    claude = project_setup(settings, "anthropic", "claude-opus-5-5", USER_KEY)
    assert claude.connection == "project" and claude.provider is AiProvider.ANTHROPIC
    assert claude.label == "Claude Opus 5.5 (Anthropic, your key)"
    assert claude.monthly_token_limit == 0 and claude.limits.max_output_tokens == 16000
    assert claude.prices.cost(1_000_000, 0) == pytest.approx(4.0)
    assert USER_KEY not in repr(claude)
    free = project_setup(settings, "unorouter", "glm-5.3:free", USER_KEY)
    assert free.provider is AiProvider.OPENAI_COMPATIBLE and free.free
    assert free.base_url == "https://api.unorouter.com/v1"
    assert free.limits.timeout_seconds == 1800 and free.limits.max_output_tokens == 8192
    plain = make_settings(ai_unorouter_base_url="http://models.example.com/v1")
    assert not project_setup(plain, "unorouter", "glm-5.3:free", USER_KEY).available


REQUEST = ModelRequest(
    system="Review.",
    messages=(
        Message("user", (TextBlock("Question?"),)),
        Message(
            "assistant",
            (
                ProviderBlock("anthropic", {"type": "thinking", "thinking": "", "signature": "s1"}),
                ToolUseBlock("t1", "read_file", {"path": "a.py"}),
            ),
        ),
        Message("user", (ToolResultBlock("t1", "line 1"),)),
    ),
    tools=(ToolSpec("submit_answer", "Submit", {"type": "object", "properties": {}}),),
    tool_choice="submit_answer",
    max_output_tokens=16000,
)


def _anthropic(model: str) -> AnthropicClient:
    return AnthropicClient(api_key=USER_KEY, model=model, options=ClientOptions())


def test_claude_requests_keep_thinking_blocks_and_avoid_forced_tool_choice_where_rejected() -> None:
    current = _anthropic("claude-opus-5-5").payload(REQUEST)
    assert current["tool_choice"] == {"type": "auto"}
    last = current["messages"][-1]["content"]
    assert last[-1] == {
        "type": "text",
        "text": "Call the submit_answer tool now with your final result. Do not call any other tool.",
    }
    assert current["messages"][1]["content"][0] == {
        "type": "thinking",
        "thinking": "",
        "signature": "s1",
    }  # sent back unchanged
    older = _anthropic("claude-opus-4-8").payload(REQUEST)
    assert older["tool_choice"] == {"type": "tool", "name": "submit_answer"}
    openai = OpenAICompatibleClient(api_key=USER_KEY, model="glm-5.3:free").payload(REQUEST)
    assert "thinking" not in json.dumps(openai["messages"])  # other providers ignore them


async def test_claude_responses_keep_thinking_blocks_in_order() -> None:
    body = {
        "model": "claude-opus-5-5",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "tool_use", "id": "t9", "name": "read_file", "input": {"path": "b.py"}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    client = AnthropicClient(
        api_key=USER_KEY,
        model="claude-opus-5-5",
        options=ClientOptions(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
    )
    response = await client.complete(REQUEST)
    assert isinstance(response.content[0], ProviderBlock)
    assert response.content[0].data["signature"] == "sig"
    assert [c.name for c in response.tool_calls] == ["read_file"]
