"""Server-side AI configuration: provider, model, credentials, limits and prices.

The API key comes from ``CRP_AI_API_KEY`` (e.g. a Render environment variable) or an owner-only
file named by ``CRP_AI_API_KEY_FILE``; it is never stored in the database, logged or returned.
``resolve`` either returns a usable configuration or a plain-language reason (for users) plus a
setup hint (for administrators) so deterministic reviews keep working without AI.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from crp_analysis.ai.providers import (
    AnthropicClient,
    ClientOptions,
    ModelClient,
    OpenAICompatibleClient,
)
from crp_core.config import AiProvider, Settings
from crp_core.local_secrets import SecretFileError, read_secret_file

DEFAULT_MODELS = {AiProvider.ANTHROPIC: "claude-opus-5-5"}
DEFAULT_BASE_URLS = {
    AiProvider.ANTHROPIC: "https://api.anthropic.com",
    AiProvider.OPENAI_COMPATIBLE: "https://api.openai.com/v1",
}


@dataclass(frozen=True, slots=True)
class Limits:
    max_model_calls: int
    max_tool_calls: int
    max_tokens: int
    timeout_seconds: int
    max_output_tokens: int
    max_cost_usd: float | None


@dataclass(frozen=True, slots=True)
class Prices:
    input_per_mtok_usd: float | None
    output_per_mtok_usd: float | None

    @property
    def known(self) -> bool:
        return self.input_per_mtok_usd is not None and self.output_per_mtok_usd is not None

    def cost(self, input_tokens: int, output_tokens: int) -> float | None:
        if self.input_per_mtok_usd is None or self.output_per_mtok_usd is None:
            return None
        return (
            input_tokens * self.input_per_mtok_usd + output_tokens * self.output_per_mtok_usd
        ) / 1_000_000


@dataclass(frozen=True, slots=True)
class AiSetup:
    provider: AiProvider
    model: str | None
    base_url: str | None
    available: bool
    reason: str | None  # for everyone: why AI is unavailable
    admin_hint: str | None  # for administrators: what to configure
    limits: Limits
    prices: Prices
    monthly_token_limit: int
    monthly_cost_limit_usd: float | None
    keep_transcripts: bool
    _api_key: str | None = field(default=None, repr=False)

    def client(self, transport: httpx.AsyncBaseTransport | None = None) -> ModelClient:
        if not self.available or self._api_key is None or self.model is None:
            raise RuntimeError("AI is not configured")
        options = ClientOptions(timeout_seconds=self._timeout, max_retries=self._retries)
        if self.provider is AiProvider.ANTHROPIC:
            return AnthropicClient(
                api_key=self._api_key,
                model=self.model,
                base_url=self.base_url or DEFAULT_BASE_URLS[AiProvider.ANTHROPIC],
                options=options,
                transport=transport,
            )
        return OpenAICompatibleClient(
            api_key=self._api_key,
            model=self.model,
            base_url=self.base_url or DEFAULT_BASE_URLS[AiProvider.OPENAI_COMPATIBLE],
            auth_header=self._auth_header,
            max_tokens_field=self._max_tokens_field,
            options=options,
            transport=transport,
        )

    _timeout: float = 120.0
    _retries: int = 2
    _auth_header: str = "bearer"
    _max_tokens_field: str = "max_completion_tokens"


def _key(settings: Settings) -> tuple[str | None, str | None]:
    if settings.ai_api_key is not None and settings.ai_api_key.get_secret_value().strip():
        return settings.ai_api_key.get_secret_value().strip(), None
    if settings.ai_api_key_file is not None:
        try:
            return read_secret_file(settings.ai_api_key_file, min_length=1), None
        except SecretFileError as exc:
            return None, f"the API key file cannot be used: {exc}"
    return None, "no API key is configured (set CRP_AI_API_KEY or CRP_AI_API_KEY_FILE)"


def resolve(settings: Settings) -> AiSetup:
    provider = settings.ai_provider
    model = settings.ai_model or DEFAULT_MODELS.get(provider)

    def setup(reason: str | None, hint: str | None, key: str | None = None) -> AiSetup:
        return AiSetup(
            provider=provider,
            model=model,
            base_url=settings.ai_base_url or DEFAULT_BASE_URLS.get(provider),
            available=key is not None,
            reason=reason,
            admin_hint=hint,
            limits=Limits(
                max_model_calls=settings.ai_run_max_model_calls,
                max_tool_calls=settings.ai_run_max_tool_calls,
                max_tokens=settings.ai_run_max_tokens,
                timeout_seconds=settings.ai_run_timeout_seconds,
                max_output_tokens=settings.ai_max_output_tokens,
                max_cost_usd=settings.ai_run_max_cost_usd,
            ),
            prices=Prices(
                settings.ai_price_input_per_mtok_usd, settings.ai_price_output_per_mtok_usd
            ),
            monthly_token_limit=settings.ai_monthly_token_limit,
            monthly_cost_limit_usd=settings.ai_monthly_cost_limit_usd,
            keep_transcripts=settings.ai_keep_transcripts,
            _api_key=key,
            _timeout=settings.ai_request_timeout_seconds,
            _retries=settings.ai_max_retries,
            _auth_header=settings.ai_openai_auth_header.value,
            _max_tokens_field=settings.ai_openai_max_tokens_field,
        )

    if provider is AiProvider.NONE:
        return setup(
            "AI review is not set up on this server.",
            "Set CRP_AI_PROVIDER to anthropic or openai_compatible and add an API key.",
        )
    if model is None:
        return setup(
            "AI review is not fully set up on this server.",
            "Set CRP_AI_MODEL to the model name your provider offers.",
        )
    key, problem = _key(settings)
    if key is None:
        return setup(
            "AI review is not fully set up on this server.",
            f"AI provider {provider.value}: {problem}.",
        )
    return setup(None, None, key)
