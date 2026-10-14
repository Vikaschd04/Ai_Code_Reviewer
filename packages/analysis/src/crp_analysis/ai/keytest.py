"""Check a project's own API key before it is saved (ADR 0026). No code is sent.

- Anthropic: ``GET /v1/models/{model}`` answers for a valid key that may use the model (no
  tokens are used).
- UnoRouter (OpenAI-compatible): one chat completion with a toy tool proves the key, the model
  and tool calling, which refactorX's agents need. Free models allow about one request per
  minute, so a quick second test may be rate limited.

Messages are plain and never contain the key or the provider's response text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from crp_analysis.ai.providers import ANTHROPIC_VERSION

TIMEOUT_SECONDS = 30.0
_CHECK_TOOL = {
    "type": "function",
    "function": {
        "name": "report_connection",
        "description": "Report that the connection works.",
        "parameters": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
    },
}


@dataclass(frozen=True, slots=True)
class KeyCheck:
    ok: bool
    message: str


def _status_message(status: int, label: str) -> str:
    if status in (401, 403):
        return f"{label} did not accept this API key (or the key may not use this model)."
    if status == 404:
        return f"{label} does not offer this model to this API key."
    if status == 429:
        return (
            f"{label} is limiting requests right now (free models allow about one request a "
            "minute). Wait a minute and try again."
        )
    if status >= 500:
        return f"{label} had a problem answering. Try again in a moment."
    return f"{label} refused the test request (HTTP {status})."


async def check_anthropic(
    key: str, model: str, base_url: str, transport: httpx.AsyncBaseTransport | None = None
) -> KeyCheck:
    try:
        async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}/v1/models/{model}",
                headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
            )
    except httpx.HTTPError:
        return KeyCheck(False, "Anthropic could not be reached. Try again in a moment.")
    if response.status_code == 200:
        return KeyCheck(True, "The key works and may use this model.")
    return KeyCheck(False, _status_message(response.status_code, "Anthropic"))


def _called_tool(body: Any) -> bool:
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False
    message = choices[0].get("message")
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    return isinstance(calls, list) and any(
        isinstance(c, dict) and (c.get("function") or {}).get("name") == "report_connection"
        for c in calls
    )


async def check_openai_compatible(
    key: str,
    model: str,
    base_url: str,
    label: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> KeyCheck:
    payload = {
        "model": model,
        "max_completion_tokens": 256,
        "messages": [
            {"role": "user", "content": "Call the report_connection tool with ok set to true."}
        ],
        "tools": [_CHECK_TOOL],
        "tool_choice": "auto",
    }
    try:
        async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
            )
    except httpx.HTTPError:
        return KeyCheck(False, f"{label} could not be reached. Try again in a moment.")
    if response.status_code != 200:
        return KeyCheck(False, _status_message(response.status_code, label))
    try:
        body = response.json()
    except ValueError:
        return KeyCheck(False, f"{label} sent an answer that could not be read.")
    if not _called_tool(body):
        return KeyCheck(
            False,
            "The key works, but this model did not call the test tool. refactorX's AI features "
            "need tool calls; choose another model.",
        )
    return KeyCheck(True, "The key works and the model can call tools.")
