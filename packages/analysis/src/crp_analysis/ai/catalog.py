"""Providers and models a project can use with its own API key (ADR 0026).

- **Anthropic**: the current Claude models (IDs and list prices per million tokens as published
  by Anthropic; checked 14 October 2026). Claude Mythos is left out (invitation only).
- **UnoRouter**: an OpenAI-compatible gateway; only its free models that are online chat models
  with tool support are offered (agents need tool calls). The list is read from UnoRouter's
  public pricing endpoint and cached; a fixed list is used when it cannot be read.

Base URLs are fixed per provider (overridable by the operator for tests); users never enter one.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

ProviderId = Literal["anthropic", "unorouter"]
PROVIDER_IDS: tuple[ProviderId, ...] = ("anthropic", "unorouter")
UNOROUTER_PRICING_URL = "https://api.unorouter.com/api/pricing"
CACHE_SECONDS = 3600
FREE_OUTPUT_CAP = 8192  # UnoRouter's free models cap output at 8K tokens


@dataclass(frozen=True, slots=True)
class CatalogModel:
    id: str
    label: str
    free: bool
    note: str | None = None
    price_input: float | None = None  # USD per million tokens
    price_output: float | None = None
    max_output_tokens: int = 16000


@dataclass(frozen=True, slots=True)
class Provider:
    id: ProviderId
    name: str
    kind: Literal["anthropic", "openai_compatible"]
    data_note: str
    key_hint: str  # where to get a key
    models: tuple[CatalogModel, ...] = ()
    # Free tiers: requests spaced and longer runs (about one request per minute).
    min_interval_seconds: float = 0.0
    run_timeout_seconds: int | None = None


ANTHROPIC_MODELS: tuple[CatalogModel, ...] = (
    CatalogModel("claude-opus-5-5", "Claude Opus 5.5", False, "Recommended", 4.0, 20.0),
    CatalogModel("claude-fable-5-1", "Claude Fable 5.1", False, "Most capable", 10.0, 50.0),
    CatalogModel("claude-sonnet-5-5", "Claude Sonnet 5.5", False, "Faster, lower cost", 2.0, 10.0),
    CatalogModel("claude-haiku-5-5", "Claude Haiku 5.5", False, "Fastest, lowest cost", 0.1, 0.5),
    CatalogModel("claude-opus-5", "Claude Opus 5", False, None, 5.0, 25.0),
    CatalogModel("claude-fable-5", "Claude Fable 5", False, None, 10.0, 50.0),
    CatalogModel("claude-sonnet-5", "Claude Sonnet 5", False, None, 2.0, 10.0),
    CatalogModel("claude-opus-4-8", "Claude Opus 4.8", False, None, 5.0, 25.0),
    CatalogModel("claude-opus-4-7", "Claude Opus 4.7", False, None, 5.0, 25.0),
    CatalogModel("claude-opus-4-6", "Claude Opus 4.6", False, None, 5.0, 25.0),
    CatalogModel("claude-sonnet-4-6", "Claude Sonnet 4.6", False, None, 3.0, 15.0),
    CatalogModel("claude-haiku-4-5", "Claude Haiku 4.5", False, None, 1.0, 5.0),
)
UNOROUTER_FALLBACK: tuple[CatalogModel, ...] = tuple(
    CatalogModel(model, model, True, None, 0.0, 0.0, FREE_OUTPUT_CAP)
    for model in ("glm-5.3:free", "gemini-3.6-flash:free", "deepseek-v4.1-flash:free")
)

ANTHROPIC = Provider(
    "anthropic",
    "Anthropic",
    "anthropic",
    "Code excerpts are sent to Anthropic under your own API key and your agreement with Anthropic.",
    "Create a key at console.anthropic.com (API keys).",
    ANTHROPIC_MODELS,
)
UNOROUTER = Provider(
    "unorouter",
    "UnoRouter",
    "openai_compatible",
    "UnoRouter forwards prompts to the upstream provider of the model, which may keep or train "
    "on them (UnoRouter's privacy policy). Use it only with code you may share, such as samples.",
    "Create a key at unorouter.com (token page); no card is needed for free models.",
    UNOROUTER_FALLBACK,
    min_interval_seconds=61.0,
    run_timeout_seconds=1800,
)


def unorouter_models(data: Any) -> list[CatalogModel]:
    """Free, online chat models with tool support from UnoRouter's pricing document."""
    items = data.get("data") if isinstance(data, dict) else None
    found: list[tuple[int, CatalogModel]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name = item.get("model_name")
        if not (isinstance(name, str) and name.endswith(":free") and item.get("is_free")):
            continue
        if not item.get("online") or "openai" not in (item.get("supported_endpoint_types") or []):
            continue
        try:
            meta = json.loads(item.get("metadata") or "{}")
        except TypeError, ValueError:
            continue
        if (
            not isinstance(meta, dict)
            or meta.get("mode") != "chat"
            or not meta.get("supportsTools")
        ):
            continue
        if "text" not in (meta.get("outputModalities") or ["text"]):
            continue
        released = meta.get("releaseTs")
        output = meta.get("maxOutputTokens")
        cap = min(output, FREE_OUTPUT_CAP) if isinstance(output, int) and output > 0 else 4096
        found.append(
            (
                released if isinstance(released, int) else 0,
                CatalogModel(name, name, True, None, 0.0, 0.0, cap),
            )
        )
    found.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [model for _, model in found]


@dataclass(slots=True)
class _Cache:
    models: list[CatalogModel] = field(default_factory=list)
    fetched_at: float = 0.0


_UNOROUTER_CACHE = _Cache()


async def live_unorouter(
    transport: httpx.AsyncBaseTransport | None = None, *, now: float | None = None
) -> tuple[list[CatalogModel], bool]:
    """UnoRouter's free tool-capable chat models (cached for an hour) and whether the list is
    live; the fixed list when the public list cannot be read."""
    clock = time.monotonic() if now is None else now
    if _UNOROUTER_CACHE.models and clock - _UNOROUTER_CACHE.fetched_at < CACHE_SECONDS:
        return list(_UNOROUTER_CACHE.models), True
    try:
        async with httpx.AsyncClient(transport=transport, timeout=10.0) as client:
            response = await client.get(UNOROUTER_PRICING_URL)
        response.raise_for_status()
        models = unorouter_models(response.json())
    except httpx.HTTPError, ValueError:
        return list(UNOROUTER_FALLBACK), False
    if not models:
        return list(UNOROUTER_FALLBACK), False
    _UNOROUTER_CACHE.models, _UNOROUTER_CACHE.fetched_at = models, clock
    return list(models), True


def provider(provider_id: str) -> Provider | None:
    return {"anthropic": ANTHROPIC, "unorouter": UNOROUTER}.get(provider_id)


def find_model(
    provider_id: str, model_id: str, live: list[CatalogModel] | None = None
) -> CatalogModel | None:
    """The model in the provider's list (UnoRouter: the live list when given)."""
    chosen = provider(provider_id)
    if chosen is None:
        return None
    models = live if provider_id == "unorouter" and live is not None else list(chosen.models)
    return next((m for m in models if m.id == model_id), None)
