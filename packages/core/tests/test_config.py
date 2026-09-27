from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from crp_core.config import Settings, describe_settings_error, is_loopback_host

DB = "postgresql+psycopg://u:p@127.0.0.1:5432/db"


def settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": DB,
        "local_token_file": tmp_path / "token",
        "artifact_root": tmp_path / "artifacts",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "[::1]", "localhost"])
def test_loopback_hosts_are_recognised(host: str) -> None:
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "10.0.0.1", "example.com", "::", ""])  # noqa: S104
def test_non_loopback_hosts_are_rejected(host: str) -> None:
    assert not is_loopback_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "devbox.example.com"])  # noqa: S104
def test_local_token_mode_refuses_non_loopback_bind(tmp_path: Path, host: str) -> None:
    with pytest.raises(ValidationError, match="Refusing to start"):
        settings(tmp_path, api_host=host)


def test_local_token_mode_refuses_non_loopback_web_origin(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="not a loopback origin"):
        settings(tmp_path, allowed_web_origins="http://127.0.0.1:5173,https://evil.example")


def test_origins_are_parsed_from_comma_separated_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRP_ALLOWED_WEB_ORIGINS", "http://127.0.0.1:1111, http://localhost:2222")
    loaded = settings(tmp_path)
    assert loaded.allowed_web_origins == ("http://127.0.0.1:1111", "http://localhost:2222")


def test_token_file_is_required_in_local_token_mode(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="CRP_LOCAL_TOKEN_FILE is required"):
        settings(tmp_path, local_token_file=None)


def test_artifact_root_inside_trusted_repo_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    with pytest.raises(ValidationError, match="outside the trusted development repository"):
        settings(tmp_path, trusted_dev_root=repo, artifact_root=repo / ".local" / "artifacts")


def test_artifact_root_cannot_be_home_directory(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="dedicated directory"):
        settings(tmp_path, artifact_root=Path.home())


def test_relative_paths_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="must be absolute"):
        settings(tmp_path, artifact_root=Path("relative/artifacts"))


def test_only_psycopg_postgres_urls_are_accepted(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="postgresql\\+psycopg"):
        settings(tmp_path, database_url="sqlite:///tmp.db")


def test_unknown_environment_such_as_hosted_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        settings(tmp_path, environment="hosted")


def test_database_url_is_not_revealed_in_repr(tmp_path: Path) -> None:
    loaded = settings(tmp_path)
    assert "u:p@" not in repr(loaded)


def test_settings_errors_are_rendered_without_input_values(tmp_path: Path) -> None:
    secret_url = "postgresql+psycopg://crp:hunter2-secret@127.0.0.1:5432/db"
    with pytest.raises(ValidationError) as info:
        settings(tmp_path, api_host="0.0.0.0", database_url=secret_url)  # noqa: S104
    rendered = describe_settings_error(info.value)
    assert "Refusing to start" in rendered
    assert "hunter2-secret" not in rendered


HOSTED: dict[str, Any] = {
    "environment": "hosted",
    "api_host": "0.0.0.0",  # noqa: S104 - hosted mode binds publicly behind a TLS proxy
    "public_hosts": "api.example.com",
    "allowed_web_origins": "https://app.example.com",
    "public_api_url": "https://api.example.com",
}


def test_hosted_mode_accepts_explicit_https_configuration(tmp_path: Path) -> None:
    hosted = settings(tmp_path, **HOSTED)
    assert hosted.hosted and hosted.public_hosts == ("api.example.com",)
    assert hosted.origin_allowed("https://app.example.com")
    assert not hosted.origin_allowed("https://evil.example.com")
    assert not hosted.origin_allowed(None)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"public_hosts": ""}, "CRP_PUBLIC_HOSTS"),
        ({"allowed_web_origins": "http://app.example.com"}, "https:// origin"),
        ({"allowed_web_origins": "https://app.example.com/path"}, "https:// origin"),
        ({"allowed_web_origin_regex": "https://.*"}, "anchored"),
        ({"public_api_url": "http://api.example.com"}, "CRP_PUBLIC_API_URL"),
        ({"local_token_file": None}, "CRP_LOCAL_TOKEN_FILE"),
    ],
)
def test_hosted_mode_rejects_weak_configuration(
    tmp_path: Path, override: dict[str, Any], message: str
) -> None:
    with pytest.raises((ValidationError, ValueError)) as caught:
        settings(tmp_path, **{**HOSTED, **override})
    assert message in str(caught.value)


def test_preview_origins_match_only_the_anchored_pattern(tmp_path: Path) -> None:
    hosted = settings(
        tmp_path,
        **HOSTED,
        allowed_web_origin_regex=r"^https://ai-code-reviewer-[a-z0-9-]+\.vercel\.app$",
    )
    assert hosted.origin_allowed("https://ai-code-reviewer-git-main-team.vercel.app")
    assert not hosted.origin_allowed("https://ai-code-reviewer-x.vercel.app.evil.com")
    assert not hosted.origin_allowed("http://ai-code-reviewer-x.vercel.app")
    local = settings(tmp_path, allowed_web_origin_regex=r"^https://x\.example$")
    assert not local.origin_allowed("https://x.example")  # patterns apply in hosted mode only


def test_local_mode_still_refuses_public_binding(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        settings(tmp_path, api_host="0.0.0.0")  # noqa: S104


def test_platform_database_urls_are_normalized(tmp_path: Path) -> None:
    for url in ("postgres://u:p@db:5432/x", "postgresql://u:p@db/x"):
        normalized = settings(tmp_path, database_url=url).database_url.get_secret_value()
        assert normalized.startswith("postgresql+psycopg://u:p@db")
    with pytest.raises(ValidationError):
        settings(tmp_path, database_url="mysql://u:p@db/x")
