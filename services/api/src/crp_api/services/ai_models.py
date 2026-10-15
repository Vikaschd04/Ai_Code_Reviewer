"""Which AI a project uses (ADR 0025, ADR 0026): a connection set up by the operator, or the
project's own provider, model and API key. The API never decrypts a project's key except to test
it before saving; runs decrypt it in the worker."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select

from crp_analysis.ai import catalog
from crp_analysis.ai.catalog import CatalogModel
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
            setup = project_setup(settings, row.provider, row.model, None)
            if not offered_now(settings, row.provider, row.model):
                return dataclasses.replace(setup, available=False, reason=not_offered(row.model))
            return setup
        placeholder = project_setup(settings, "anthropic", "claude-opus-5-5", None)
        return dataclasses.replace(
            placeholder, label="Your own AI model", available=False, reason=NO_MODEL
        )
    connection = cast(Connection, policy.connection) if policy else "primary"
    return resolve(settings, connection)


def not_offered(model: str) -> str:
    return (
        f"UnoRouter is not offering {model} right now (its free models change often). "
        "Choose another model in Settings (AI)."
    )


def offered_now(
    settings: Settings, provider_id: str, model: str, live: list[CatalogModel] | None = None
) -> bool:
    """False only when UnoRouter's live list is known (given, or read within the hour) and lacks
    the model; free models disappear while their upstream providers are busy or withdrawn."""
    if provider_id != "unorouter" or not settings.ai_catalog_live:
        return True
    known = live if live is not None else catalog.cached_unorouter()
    return known is None or any(m.id == model for m in known)


async def recent_key_tests(session: Any, project_id: uuid.UUID) -> int:
    """Key tests of the project in the last hour (each test is an audited policy event)."""
    since = datetime.now(UTC) - timedelta(hours=1)
    rows = await session.scalars(
        select(AiPolicyEvent.changes).where(
            AiPolicyEvent.project_id == project_id, AiPolicyEvent.created_at >= since
        )
    )
    return sum(1 for changes in rows.all() if isinstance(changes, dict) and "key_test" in changes)
