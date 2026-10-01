"""Application dependency container built during startup and attached to ``app.state``."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from crp_analysis.sources.github import GitHubClient
from crp_api.auth.local_token import LocalTokenProvider
from crp_api.auth.throttle import FailedAttemptThrottle
from crp_core.artifacts import ArtifactStore
from crp_core.config import Settings
from crp_core.workflows.gateway import WorkflowGateway


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    workflows: WorkflowGateway
    artifacts: ArtifactStore
    identity: LocalTokenProvider
    login_throttle: FailedAttemptThrottle
    github: GitHubClient | None = None  # set when a GitHub App is configured (P06)


def get_container(request: Request) -> AppContainer:
    container: AppContainer = request.app.state.container
    return container
