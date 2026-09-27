"""Migration, local-identity provisioning and labelled synthetic fixture seeding."""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from crp_core.config import Settings
from crp_core.db import migrate
from crp_core.db.identity import LocalIdentity, ensure_local_identity
from crp_core.db.models import Project
from crp_core.db.session import create_engine_from_settings, create_session_factory, transaction
from crp_core.domain.states import ProjectOrigin

SYNTHETIC_PROJECT_SLUG = "synthetic-foundation-sample"
SYNTHETIC_PROJECT_NAME = "Synthetic fixture: foundation sample"
SYNTHETIC_PROJECT_DESCRIPTION = (
    "Synthetic, clearly labelled fixture created by `make seed-fixtures`. It has no source "
    "snapshot, scans or findings; it only exercises project listing."
)


def settings_from_env(env: dict[str, str]) -> Settings:
    return Settings(**{key.removeprefix("CRP_").lower(): value for key, value in env.items()})  # type: ignore[arg-type]


async def _provision(settings: Settings) -> LocalIdentity:
    engine = create_engine_from_settings(settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            return await ensure_local_identity(session)
    finally:
        await engine.dispose()


def migrate_and_provision(settings: Settings) -> tuple[str | None, LocalIdentity]:
    url = settings.database_url.get_secret_value()
    migrate.upgrade(url)
    identity = asyncio.run(_provision(settings))
    return migrate.database_revision(url), identity


async def _seed(settings: Settings) -> bool:
    engine = create_engine_from_settings(settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            identity = await ensure_local_identity(session)
            result = await session.execute(
                insert(Project)
                .values(
                    workspace_id=identity.workspace_id,
                    slug=SYNTHETIC_PROJECT_SLUG,
                    name=SYNTHETIC_PROJECT_NAME,
                    description=SYNTHETIC_PROJECT_DESCRIPTION,
                    origin=ProjectOrigin.SYNTHETIC_FIXTURE.value,
                    created_by=identity.user_id,
                )
                .on_conflict_do_nothing(index_elements=[Project.workspace_id, Project.slug])
                .returning(Project.id)
            )
            created = result.scalar_one_or_none() is not None
            existing = (
                await session.execute(
                    select(Project.origin).where(
                        Project.workspace_id == identity.workspace_id,
                        Project.slug == SYNTHETIC_PROJECT_SLUG,
                    )
                )
            ).scalar_one()
            if existing != ProjectOrigin.SYNTHETIC_FIXTURE.value:
                raise RuntimeError(
                    f"a non-fixture project already uses slug '{SYNTHETIC_PROJECT_SLUG}'"
                )
            return created
    finally:
        await engine.dispose()


def seed_fixtures(settings: Settings) -> bool:
    """Create the labelled synthetic project if absent. Returns True when it was created."""
    return asyncio.run(_seed(settings))
