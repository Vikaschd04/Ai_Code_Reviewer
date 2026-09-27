"""Locations used by trusted development tooling."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class RepoRootNotFoundError(RuntimeError):
    pass


def find_repo_root(start: Path | None = None) -> Path:
    """Locate the development repository (directory holding AGENTS.md and the uv workspace)."""
    override = os.environ.get("CRP_REPO_ROOT")
    if override:
        return Path(override).resolve()
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "AGENTS.md").is_file() and (candidate / "pyproject.toml").is_file():
            return candidate
    raise RepoRootNotFoundError("run this command inside the code-review-platform repository")


@dataclass(frozen=True, slots=True)
class DevPaths:
    """Ignored local state (``.local/``): secrets, service data, logs, reports and context cache."""

    repo: Path

    @property
    def state(self) -> Path:
        return self.repo / ".local"

    @property
    def secrets(self) -> Path:
        return self.state / "secrets"

    @property
    def token_file(self) -> Path:
        return self.secrets / "local-api-token"

    @property
    def postgres_password_file(self) -> Path:
        return self.secrets / "postgres-password"

    @property
    def postgres_data(self) -> Path:
        return self.state / "postgres"

    @property
    def temporal_db(self) -> Path:
        return self.state / "temporal" / "dev.sqlite"

    @property
    def run(self) -> Path:
        return self.state / "run"

    @property
    def logs(self) -> Path:
        return self.state / "logs"

    @property
    def env_file(self) -> Path:
        return self.state / "dev.env"

    @property
    def test_reports(self) -> Path:
        return self.state / "test-reports"

    @property
    def context_cache(self) -> Path:
        return self.state / "context"
