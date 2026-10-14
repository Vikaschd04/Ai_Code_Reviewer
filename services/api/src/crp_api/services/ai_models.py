"""Which AI a project uses (ADR 0025, ADR 0026): a connection set up by the operator, or the
project's own provider, model and API key. The API never decrypts a project's key except to test
it before saving; runs decrypt it in the worker."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select

from crp_analysis.ai.config import AiSetup, Connection, project_setup, resolve
from crp_core.config import Settings
from crp_core.db.models import AiPolicyEvent, ProjectAiModel, ProjectAiPolicy

MAX_KEY_TESTS_PER_HOUR = 10
NO_MODEL = "Choose an AI model and add your API key in Settings (AI) to use AI in this project."


async def project_model(session: Any, project_id: uuid.UUID) -> ProjectAiModel | None:
    row: ProjectAiModel | None = await session.get(ProjectAiModel, project_id)
    return row


async def connection_setup(
    session: Any, settings: Settings, policy: ProjectAiPolicy | None
) -> AiSetup:
    """The AI configuration a project's runs use (no key: the API does not call the provider)."""
    if policy is not None and policy.connection == "project":
        row = await project_model(session, policy.project_id)
        if row is not None:
            return project_setup(settings, row.provider, row.model, None)
        placeholder = project_setup(settings, "anthropic", "claude-opus-5-5", None)
        return dataclasses.replace(
            placeholder, label="Your own AI model", available=False, reason=NO_MODEL
        )
    connection = cast(Connection, policy.connection) if policy else "primary"
    return resolve(settings, connection)


async def recent_key_tests(session: Any, project_id: uuid.UUID) -> int:
    """Key tests of the project in the last hour (each test is an audited policy event)."""
    since = datetime.now(UTC) - timedelta(hours=1)
    rows = await session.scalars(
        select(AiPolicyEvent.changes).where(
            AiPolicyEvent.project_id == project_id, AiPolicyEvent.created_at >= since
        )
    )
    return sum(1 for changes in rows.all() if isinstance(changes, dict) and "key_test" in changes)
