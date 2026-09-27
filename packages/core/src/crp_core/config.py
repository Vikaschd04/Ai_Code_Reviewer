"""Typed runtime configuration shared by the API, worker and developer tools.

All settings come from ``CRP_``-prefixed environment variables. Defaults live here and nowhere
else. Validation runs on construction so that unsafe combinations (for example local-token
authentication bound to a non-loopback address) fail at startup instead of at first request.
"""

from __future__ import annotations

import ipaddress
import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LOOPBACK_HOSTNAMES = frozenset({"localhost"})


class InsecureConfigurationError(ValueError):
    """A configuration would expose an unauthenticated or weakly authenticated service."""


class DeploymentEnvironment(StrEnum):
    """Supported deployment tiers. Hosted multi-tenant deployment is absent until P07.

    ``hosted`` is a single-user deployment behind an HTTPS reverse proxy (see
    docs/DEPLOYMENT.md): public bind, Host-header allowlist, HTTPS-only web origins and secure
    cookies. It is not a multi-tenant or SSO deployment.
    """

    LOCAL = "local"
    TEST = "test"
    HOSTED = "hosted"


class AuthMode(StrEnum):
    """Identity provider boundary. Only the generated local token is implemented."""

    LOCAL_TOKEN = "local_token"  # noqa: S105 - enum label, not a credential


class ArtifactBackend(StrEnum):
    FILESYSTEM = "filesystem"


def is_loopback_host(host: str) -> bool:
    """Return True when ``host`` names only the local machine (IPv4/IPv6 loopback or localhost)."""
    candidate = host.strip().strip("[]").lower()
    if candidate in LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def default_data_dir() -> Path:
    """Per-user data directory for customer artifacts, deliberately outside the development repo."""
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "code-review-platform"


class Settings(BaseSettings):
    """Validated platform settings. See ``.env.example`` for descriptions of each variable."""

    model_config = SettingsConfigDict(env_prefix="CRP_", extra="ignore", frozen=True)

    environment: DeploymentEnvironment = DeploymentEnvironment.LOCAL
    auth_mode: AuthMode = AuthMode.LOCAL_TOKEN

    api_host: str = "127.0.0.1"
    api_port: Annotated[int, Field(ge=1, le=65535)] = 8710
    allowed_web_origins: Annotated[tuple[str, ...], NoDecode] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )

    local_token_file: Path | None = None
    # Hosted mode only: Host header allowlist, preview-origin pattern and the backend's own public
    # HTTPS URL (used for direct archive uploads that bypass the web host's proxy).
    public_hosts: Annotated[tuple[str, ...], NoDecode] = ()
    allowed_web_origin_regex: str | None = None
    public_api_url: str | None = None
    session_ttl_seconds: Annotated[int, Field(ge=300, le=7 * 24 * 3600)] = 12 * 3600

    database_url: SecretStr
    database_pool_size: Annotated[int, Field(ge=1, le=50)] = 5
    database_connect_timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 5.0

    temporal_address: str = "127.0.0.1:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "crp-main"
    temporal_connect_timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 5.0
    worker_poller_fresh_seconds: Annotated[int, Field(ge=10, le=600)] = 90

    artifact_backend: ArtifactBackend = ArtifactBackend.FILESYSTEM
    artifact_root: Path = Field(default_factory=lambda: default_data_dir() / "artifacts")
    artifact_max_object_bytes: Annotated[int, Field(ge=1024)] = 512 * 1024 * 1024

    trusted_dev_root: Path | None = None
    readiness_check_timeout_seconds: Annotated[float, Field(gt=0, le=30)] = 3.0

    # Intake quotas (SOURCE_INTAKE.md development defaults; policy, not performance promises).
    intake_max_upload_bytes: Annotated[int, Field(ge=1024)] = 100 * 1024 * 1024
    intake_max_expanded_bytes: Annotated[int, Field(ge=1024)] = 1024 * 1024 * 1024
    intake_max_entries: Annotated[int, Field(ge=1, le=1_000_000)] = 50_000
    intake_max_text_file_bytes: Annotated[int, Field(ge=1024)] = 10 * 1024 * 1024
    intake_max_compression_ratio: Annotated[int, Field(ge=2, le=10_000)] = 100
    intake_max_path_length: Annotated[int, Field(ge=64, le=4096)] = 1024
    intake_max_path_depth: Annotated[int, Field(ge=4, le=512)] = 64
    intake_client_manifest_max_bytes: Annotated[int, Field(ge=1024)] = 16 * 1024 * 1024
    intake_expiry_seconds: Annotated[int, Field(ge=300)] = 24 * 3600

    # Analysis execution (worker side). Work directories hold materialized snapshot copies.
    work_root: Path = Field(default_factory=lambda: default_data_dir() / "work")
    pmd_home: Path | None = None
    opengrep_home: Path | None = None
    trivy_home: Path | None = None
    trivy_cache_dir: Path | None = None
    eslint_runner_dir: Path | None = None
    node_executable: Path | None = None
    engine_timeout_seconds: Annotated[int, Field(ge=5, le=6 * 3600)] = 900
    engine_max_output_bytes: Annotated[int, Field(ge=1024)] = 64 * 1024 * 1024
    pmd_java_heap: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,4}[mMgG]$")] = "1g"
    structure_max_symbols_per_file: Annotated[int, Field(ge=10, le=100_000)] = 2000

    log_level: str = "INFO"
    log_format: str = "json"

    @property
    def hosted(self) -> bool:
        return self.environment is DeploymentEnvironment.HOSTED

    @field_validator("allowed_web_origins", "public_hosts", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator(
        "artifact_root",
        "local_token_file",
        "trusted_dev_root",
        "work_root",
        "pmd_home",
        "opengrep_home",
        "trivy_home",
        "trivy_cache_dir",
        "eslint_runner_dir",
        "node_executable",
    )
    @classmethod
    def _absolute_paths(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        expanded = value.expanduser()
        if not expanded.is_absolute():
            raise ValueError(f"path settings must be absolute, got a relative path: {value}")
        return expanded

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, value: object) -> object:
        """Accept the ``postgres://``/``postgresql://`` URLs that hosting platforms provide."""
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(raw, str):
            for prefix in ("postgres://", "postgresql://"):
                if raw.startswith(prefix):
                    return "postgresql+psycopg://" + raw[len(prefix) :]
        return value

    @field_validator("database_url")
    @classmethod
    def _postgres_only(cls, value: SecretStr) -> SecretStr:
        scheme = urlsplit(value.get_secret_value()).scheme
        if scheme != "postgresql+psycopg":
            raise ValueError("CRP_DATABASE_URL must use the postgresql+psycopg:// scheme")
        return value

    @field_validator("log_format")
    @classmethod
    def _log_format(cls, value: str) -> str:
        if value not in {"json", "text"}:
            raise ValueError("CRP_LOG_FORMAT must be 'json' or 'text'")
        return value

    @model_validator(mode="after")
    def _enforce_local_boundary(self) -> Self:
        if self.hosted:
            self._enforce_hosted_boundary()
        elif self.auth_mode is AuthMode.LOCAL_TOKEN:
            if not is_loopback_host(self.api_host):
                raise InsecureConfigurationError(
                    "Refusing to start: local-token authentication may only bind to a loopback "
                    f"address, but CRP_API_HOST={self.api_host!r}. Hosted exposure requires a "
                    "reviewed identity provider (planned for P07)."
                )
            for origin in self.allowed_web_origins:
                parts = urlsplit(origin)
                if parts.scheme not in {"http", "https"} or not is_loopback_host(
                    parts.hostname or ""
                ):
                    raise InsecureConfigurationError(
                        f"CRP_ALLOWED_WEB_ORIGINS entry {origin!r} is not a loopback origin"
                    )
            if self.local_token_file is None:
                raise ValueError("CRP_LOCAL_TOKEN_FILE is required when CRP_AUTH_MODE=local_token")

        if self.allowed_web_origin_regex is not None:
            try:
                re.compile(self.allowed_web_origin_regex)
            except re.error as exc:
                raise ValueError("CRP_ALLOWED_WEB_ORIGIN_REGEX is not a valid pattern") from exc

        root = self.artifact_root.resolve()
        if root == Path(root.anchor) or root == Path.home().resolve():
            raise InsecureConfigurationError(
                "CRP_ARTIFACT_ROOT must be a dedicated directory, not a filesystem or home root"
            )
        if self.trusted_dev_root is not None:
            trusted = self.trusted_dev_root.resolve()
            for label, candidate in (
                ("CRP_ARTIFACT_ROOT", root),
                ("CRP_WORK_ROOT", self.work_root),
            ):
                resolved = candidate.resolve()
                if resolved == trusted or trusted in resolved.parents:
                    raise InsecureConfigurationError(
                        f"{label} must be outside the trusted development repository so that "
                        "untrusted customer source never mixes with project instructions"
                    )
        return self

    def _enforce_hosted_boundary(self) -> None:
        """Single-user hosted mode: TLS-terminating proxy in front, explicit hosts and origins."""
        if self.local_token_file is None:
            raise ValueError("CRP_LOCAL_TOKEN_FILE is required in hosted mode")
        if not self.public_hosts:
            raise InsecureConfigurationError(
                "Hosted mode requires CRP_PUBLIC_HOSTS (the Host names this API answers to)"
            )
        if not self.allowed_web_origins:
            raise InsecureConfigurationError("Hosted mode requires CRP_ALLOWED_WEB_ORIGINS")
        for origin in self.allowed_web_origins:
            parts = urlsplit(origin)
            if parts.scheme != "https" or not parts.hostname or parts.path not in {"", "/"}:
                raise InsecureConfigurationError(
                    f"CRP_ALLOWED_WEB_ORIGINS entry {origin!r} must be an https:// origin"
                )
        if (
            self.allowed_web_origin_regex is not None
            and not self.allowed_web_origin_regex.startswith("^https://")
        ):
            raise InsecureConfigurationError(
                "CRP_ALLOWED_WEB_ORIGIN_REGEX must be anchored and start with ^https://"
            )
        if self.public_api_url is not None:
            parts = urlsplit(self.public_api_url)
            if parts.scheme != "https" or not parts.hostname or parts.path not in {"", "/"}:
                raise InsecureConfigurationError("CRP_PUBLIC_API_URL must be an https:// origin")

    def origin_allowed(self, origin: str | None) -> bool:
        """Exact configured origin, or (hosted) a match of the anchored preview-origin pattern."""
        if origin is None:
            return False
        if origin in self.allowed_web_origins:
            return True
        return (
            self.hosted
            and self.allowed_web_origin_regex is not None
            and re.fullmatch(self.allowed_web_origin_regex, origin) is not None
        )


def load_settings() -> Settings:
    """Load settings from the environment; raises a validation error on unsafe/missing values."""
    return Settings()  # required values come from the environment


def describe_settings_error(exc: ValidationError) -> str:
    """Render a settings validation error without echoing input values (which may hold secrets)."""
    parts = []
    for error in exc.errors(include_url=False, include_input=False, include_context=False):
        location = ".".join(str(item) for item in error["loc"]) or "settings"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)
