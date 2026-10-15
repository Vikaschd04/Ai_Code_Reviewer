"""Check a project's own API key before it is saved (ADR 0026). No code is sent.

- Anthropic: ``GET /v1/models/{model}`` answers for a valid key that may use the model (no
  tokens are used).
- UnoRouter (OpenAI-compatible): ``GET /models`` with the key answers only for a valid key and
  lists the models it may use. It does not use the model's rate limit (free models allow about
  one request a minute), so the first AI run right after saving is not held back. Tool support
  comes from the catalog, which offers only models that call tools.

Messages are plain and never contain the key or the provider's response text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from crp_analysis.ai.providers import ANTHROPIC_VERSION

TIMEOUT_SECONDS = 30.0


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


def _model_ids(body: Any) -> set[str]:
    items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return set()
    return {
        item["id"] for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


async def check_openai_compatible(
    key: str,
    model: str,
    base_url: str,
    label: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> KeyCheck:
    try:
        async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"}
            )
    except httpx.HTTPError:
        return KeyCheck(False, f"{label} could not be reached. Try again in a moment.")
    if response.status_code != 200:
        return KeyCheck(False, _status_message(response.status_code, label))
    try:
        offered = _model_ids(response.json())
    except ValueError:
        return KeyCheck(False, f"{label} sent an answer that could not be read.")
    if model not in offered:
        return KeyCheck(False, f"{label} does not offer this model to this API key.")
    return KeyCheck(True, "The key works and may use this model.")
