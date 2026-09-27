"""Environment translation for the single-container hosted deployment."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from crp_devtools.dbtasks import settings_from_env
from crp_devtools.hosted import hosted_env
from crp_devtools.infra import InfraError

REPO = Path(__file__).resolve().parents[3]
TOKEN = "t" * 43


def _environ(tmp_path: Path, **overrides: str) -> dict[str, str]:
    values = {
        "CRP_ACCESS_TOKEN": TOKEN,
        "RENDER_EXTERNAL_HOSTNAME": "ai-code-reviewer-api.onrender.com",
        "CRP_ALLOWED_WEB_ORIGINS": "https://ai-code-reviewer.vercel.app",
        "DATABASE_URL": "postgresql://user:pw@dpg-internal/crp",
        "CRP_DATA_DIR": str(tmp_path / "data"),
        "PORT": "10000",
        "CRP_INTAKE_MAX_UPLOAD_BYTES": "52428800",
    }
    values.update(overrides)
    return {k: v for k, v in values.items() if v}


def test_hosted_env_produces_valid_hosted_settings(tmp_path: Path) -> None:
    env, data = hosted_env(_environ(tmp_path), REPO)
    settings = settings_from_env(env)
    assert settings.hosted
    assert settings.public_hosts == ("ai-code-reviewer-api.onrender.com",)
    assert settings.public_api_url == "https://ai-code-reviewer-api.onrender.com"
    assert settings.api_port == 10000 and settings.api_host == "0.0.0.0"  # noqa: S104
    assert settings.database_url.get_secret_value().startswith("postgresql+psycopg://")
    assert settings.intake_max_upload_bytes == 52428800
    assert settings.artifact_root == data / "artifacts"
    token_file = data / "secrets" / "access-token"
    assert token_file.read_text().strip() == TOKEN
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert "CRP_ACCESS_TOKEN" not in env  # the raw secret never reaches child processes


def test_token_rotation_overwrites_the_token_file(tmp_path: Path) -> None:
    hosted_env(_environ(tmp_path), REPO)
    _, data = hosted_env(_environ(tmp_path, CRP_ACCESS_TOKEN="r" * 40), REPO)
    assert (data / "secrets" / "access-token").read_text().strip() == "r" * 40


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"CRP_ACCESS_TOKEN": ""}, "CRP_ACCESS_TOKEN"),
        ({"CRP_ACCESS_TOKEN": "short"}, "CRP_ACCESS_TOKEN"),
        ({"RENDER_EXTERNAL_HOSTNAME": ""}, "CRP_PUBLIC_HOST"),
        ({"CRP_ALLOWED_WEB_ORIGINS": ""}, "CRP_ALLOWED_WEB_ORIGINS"),
        ({"DATABASE_URL": ""}, "CRP_DATABASE_URL"),
    ],
)
def test_missing_or_weak_configuration_refuses_to_start(
    tmp_path: Path, override: dict[str, str], message: str
) -> None:
    with pytest.raises(InfraError, match=message):
        hosted_env(_environ(tmp_path, **override), REPO)
