"""Server-side AI configuration: provider, model, credentials, limits and prices.

Two connections (ADR 0025): ``primary`` (``CRP_AI_*``, for example Anthropic Claude) and an
optional ``alt`` (``CRP_AI_ALT_*``, for example a free or cheaper OpenAI-compatible model used for
testing). Each project's admin chooses which one receives the project's code. API keys come from
the environment (e.g. Render environment variables) or owner-only files; they are never stored
in the database, logged or returned. ``resolve`` either returns a usable configuration or a
plain-language reason (for users) plus a setup hint (for administrators) so deterministic reviews
keep working without AI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from crp_analysis.ai import catalog
from crp_analysis.ai.providers import (
    AnthropicClient,
    ClientOptions,
    ModelClient,
    OpenAICompatibleClient,
)
from crp_core.config import AiProvider, Settings
from crp_core.local_secrets import SecretFileError, read_secret_file

Connection = Literal["primary", "alt"]  # set up by the operator (environment)
CONNECTIONS: tuple[Connection, ...] = ("primary", "alt")
RunConnection = Literal["primary", "alt", "project"]  # "project": the project's own key (ADR 0026)

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
    connection: RunConnection
    label: str  # what users see, for example "Claude (Anthropic)" or "Free test model"
    data_note: str | None  # how the provider handles code it receives, shown before opting in
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
        options = ClientOptions(
            timeout_seconds=self._timeout,
            max_retries=self._retries,
            min_interval_seconds=self._min_interval,
            max_retry_after_seconds=self._max_retry_after,
        )
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

    @property
    def free(self) -> bool:
        """Both prices are configured as zero (a free tier)."""
        return self.prices.input_per_mtok_usd == 0 and self.prices.output_per_mtok_usd == 0

    _timeout: float = 120.0
    _retries: int = 2
    _min_interval: float = 0.0
    _max_retry_after: float = 30.0
    _auth_header: str = "bearer"
    _max_tokens_field: str = "max_completion_tokens"


@dataclass(frozen=True, slots=True)
class _Source:
    """The settings of one connection (the primary's limits are the alternate's defaults)."""

    env: str  # environment variable prefix, for hints
    provider: AiProvider
    model: str | None
    base_url: str | None
    api_key: SecretStr | None
    api_key_file: Path | None
    label: str | None
    data_note: str | None
    max_output_tokens: int
    run_timeout_seconds: int
    monthly_token_limit: int
    price_input: float | None
    price_output: float | None
    max_retries: int
    min_interval: float
    max_retry_after: float


def _source(settings: Settings, connection: Connection) -> _Source:
    if connection == "primary":
        return _Source(
            "CRP_AI",
            settings.ai_provider,
            settings.ai_model,
            settings.ai_base_url,
            settings.ai_api_key,
            settings.ai_api_key_file,
            settings.ai_label,
            settings.ai_data_note,
            settings.ai_max_output_tokens,
            settings.ai_run_timeout_seconds,
            settings.ai_monthly_token_limit,
            settings.ai_price_input_per_mtok_usd,
            settings.ai_price_output_per_mtok_usd,
            settings.ai_max_retries,
            0.0,
            30.0,
        )
    return _Source(
        "CRP_AI_ALT",
        settings.ai_alt_provider,
        settings.ai_alt_model,
        settings.ai_alt_base_url,
        settings.ai_alt_api_key,
        settings.ai_alt_api_key_file,
        settings.ai_alt_label,
        settings.ai_alt_data_note,
        settings.ai_alt_max_output_tokens or settings.ai_max_output_tokens,
        settings.ai_alt_run_timeout_seconds or settings.ai_run_timeout_seconds,
        settings.ai_alt_monthly_token_limit
        if settings.ai_alt_monthly_token_limit is not None
        else settings.ai_monthly_token_limit,
        settings.ai_alt_price_input_per_mtok_usd,
        settings.ai_alt_price_output_per_mtok_usd,
        settings.ai_alt_max_retries
        if settings.ai_alt_max_retries is not None
        else settings.ai_max_retries,
        settings.ai_alt_min_interval_seconds,
        settings.ai_alt_max_retry_after_seconds,
    )


def _key(source: _Source) -> tuple[str | None, str | None]:
    if source.api_key is not None and source.api_key.get_secret_value().strip():
        return source.api_key.get_secret_value().strip(), None
    if source.api_key_file is not None:
        try:
            return read_secret_file(source.api_key_file, min_length=1), None
        except SecretFileError as exc:
            return None, f"the API key file cannot be used: {exc}"
    return None, (
        f"no API key is configured (set {source.env}_API_KEY or {source.env}_API_KEY_FILE)"
    )


def _encrypted_or_local(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.hostname:
        return True
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _label(source: _Source, model: str | None) -> str:
    if source.label:
        return source.label
    vendor = "Anthropic" if source.provider is AiProvider.ANTHROPIC else "OpenAI-compatible"
    return f"{model} ({vendor})" if model else vendor


def resolve(settings: Settings, connection: Connection = "primary") -> AiSetup:
    source = _source(settings, connection)
    provider = source.provider
    model = source.model or DEFAULT_MODELS.get(provider)
    env = source.env

    def setup(reason: str | None, hint: str | None, key: str | None = None) -> AiSetup:
        return AiSetup(
            connection=connection,
            label=_label(source, model),
            data_note=source.data_note,
            provider=provider,
            model=model,
            base_url=source.base_url or DEFAULT_BASE_URLS.get(provider),
            available=key is not None,
            reason=reason,
            admin_hint=hint,
            limits=Limits(
                max_model_calls=settings.ai_run_max_model_calls,
                max_tool_calls=settings.ai_run_max_tool_calls,
                max_tokens=settings.ai_run_max_tokens,
                timeout_seconds=source.run_timeout_seconds,
                max_output_tokens=source.max_output_tokens,
                max_cost_usd=settings.ai_run_max_cost_usd,
            ),
            prices=Prices(source.price_input, source.price_output),
            monthly_token_limit=source.monthly_token_limit,
            monthly_cost_limit_usd=settings.ai_monthly_cost_limit_usd,
            keep_transcripts=settings.ai_keep_transcripts,
            _api_key=key,
            _timeout=settings.ai_request_timeout_seconds,
            _retries=source.max_retries,
            _min_interval=source.min_interval,
            _max_retry_after=source.max_retry_after,
            _auth_header=settings.ai_openai_auth_header.value,
            _max_tokens_field=settings.ai_openai_max_tokens_field,
        )

    if provider is AiProvider.NONE:
        return setup(
            "AI review is not set up on this server.",
            f"Set {env}_PROVIDER to anthropic or openai_compatible and add an API key.",
        )
    if model is None:
        return setup(
            "AI review is not fully set up on this server.",
            f"Set {env}_MODEL to the model name your provider offers.",
        )
    base_url = source.base_url or DEFAULT_BASE_URLS.get(provider) or ""
    if not _encrypted_or_local(base_url):
        return setup(
            "AI review is not fully set up on this server.",
            f"{env}_BASE_URL must use https:// (plain http is allowed only for localhost), "
            "otherwise the API key and code excerpts would travel unencrypted.",
        )
    key, problem = _key(source)
    if key is None:
        return setup(
            "AI review is not fully set up on this server.",
            f"AI provider {provider.value}: {problem}.",
        )
    return setup(None, None, key)


def configured(settings: Settings) -> list[AiSetup]:
    """The connections the operator set up (a provider chosen), usable or not."""
    found = [resolve(settings, c) for c in CONNECTIONS]
    return [s for s in found if s.provider is not AiProvider.NONE]


def default_connection(settings: Settings) -> Connection:
    """The connection new project policies start with: the configured default if usable,
    otherwise the first usable one, otherwise the primary."""
    if resolve(settings, settings.ai_default_connection).available:
        return settings.ai_default_connection
    return next((c for c in CONNECTIONS if resolve(settings, c).available), "primary")


def project_setup(settings: Settings, provider_id: str, model_id: str, key: str | None) -> AiSetup:
    """The configuration for a project's own provider, model and key (ADR 0026). ``key`` is
    None where only the configuration is needed (the API never calls the provider for runs)."""
    chosen = catalog.provider(provider_id)
    if chosen is None:
        raise ValueError(f"unknown AI provider {provider_id!r}")
    model = catalog.find_model(provider_id, model_id) or catalog.CatalogModel(
        model_id, model_id, True, None, 0.0, 0.0, catalog.FREE_OUTPUT_CAP
    )
    anthropic = chosen.kind == "anthropic"
    base_url = settings.ai_anthropic_base_url if anthropic else settings.ai_unorouter_base_url
    secure = _encrypted_or_local(base_url)
    return AiSetup(
        connection="project",
        label=f"{model.label} ({chosen.name}, your key)",
        data_note=chosen.data_note,
        provider=AiProvider.ANTHROPIC if anthropic else AiProvider.OPENAI_COMPATIBLE,
        model=model.id,
        base_url=base_url,
        available=secure,
        reason=None if secure else "The provider address is not encrypted (https).",
        admin_hint=None,
        limits=Limits(
            max_model_calls=settings.ai_run_max_model_calls,
            max_tool_calls=settings.ai_run_max_tool_calls,
            max_tokens=settings.ai_run_max_tokens,
            timeout_seconds=chosen.run_timeout_seconds or settings.ai_run_timeout_seconds,
            max_output_tokens=model.max_output_tokens,
            max_cost_usd=settings.ai_run_max_cost_usd,
        ),
        prices=Prices(model.price_input, model.price_output),
        monthly_token_limit=0,  # the project's own key: its owner's budget, not the server's
        monthly_cost_limit_usd=None,
        keep_transcripts=settings.ai_keep_transcripts,
        _api_key=key if secure else None,
        _timeout=settings.ai_request_timeout_seconds,
        _retries=max(settings.ai_max_retries, 3) if model.free else settings.ai_max_retries,
        _min_interval=chosen.min_interval_seconds,
        _max_retry_after=90.0 if model.free else 30.0,
        _auth_header="bearer",
        _max_tokens_field="max_completion_tokens",
    )


def project_key_context(project_id: object, provider_id: str) -> str:
    """Associated data that binds an encrypted project key to its project and provider."""
    return f"project-ai-key:{project_id}:{provider_id}"
