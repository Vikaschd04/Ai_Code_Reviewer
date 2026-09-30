"""Permanent project deletion and reclamation of the storage it used.

Database rows go with one ``DELETE`` (every project-scoped table cascades from ``projects``).
Artifacts that belong to the project alone (intake archives, snapshot manifests, raw engine
reports) are deleted by prefix. File contents are content-addressed ``blobs/`` shared by every
snapshot with identical bytes, so they are removed only when no remaining file entry references
them — and only while no intake is being validated, under a SHARE lock on ``intakes`` that keeps
new validations from starting (a validating intake may be about to reuse an existing blob).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crp_api.auth.principal import Principal
from crp_api.errors import ApiError
from crp_api.services.scope import get_scoped
from crp_core.artifacts import ArtifactKey, ArtifactStore
from crp_core.db.models import AiRun, FileEntry, Intake, Project, Scan, Snapshot
from crp_core.db.session import transaction
from crp_core.domain.states import IntakeState, MembershipRole, ScanState

logger = logging.getLogger(__name__)

BUSY_SCAN_STATES = (ScanState.QUEUED.value, ScanState.RUNNING.value)
BUSY_INTAKE_STATES = (IntakeState.VALIDATING.value,)
_BLOB_BATCH = 500


@dataclass(frozen=True, slots=True)
class DeletionResult:
    project_id: uuid.UUID
    artifacts_deleted: int
    blobs_deleted: int
    blob_sweep_deferred: bool


def _can_delete(principal: Principal, project: Project) -> bool:
    """Admins and owners may delete any project of their workspace; members only their own."""
    if principal.has_role(project.workspace_id, MembershipRole.ADMIN):
        return True
    return (
        principal.has_role(project.workspace_id, MembershipRole.MEMBER)
        and project.created_by is not None
        and project.created_by == principal.user_id
    )


async def _busy(session: AsyncSession, project_id: uuid.UUID) -> bool:
    scans = await session.scalar(
        select(func.count())
        .select_from(Scan)
        .where(Scan.project_id == project_id, Scan.state.in_(BUSY_SCAN_STATES))
    )
    intakes = await session.scalar(
        select(func.count())
        .select_from(Intake)
        .where(Intake.project_id == project_id, Intake.state.in_(BUSY_INTAKE_STATES))
    )
    return bool(scans or intakes)


def _delete_keys(store: ArtifactStore, keys: list[ArtifactKey]) -> int:
    return sum(1 for key in keys if store.delete(key))


def _delete_prefixes(store: ArtifactStore, prefixes: list[str]) -> int:
    deleted = 0
    for prefix in prefixes:
        for key in store.list_keys(prefix):
            if store.delete(key):
                deleted += 1
    return deleted


async def delete_project(
    session_factory: async_sessionmaker[AsyncSession],
    store: ArtifactStore,
    principal: Principal,
    project_id: uuid.UUID,
) -> DeletionResult:
    async with transaction(session_factory) as session:
        project = await get_scoped(
            session,
            principal,
            Project,
            project_id,
            not_found="project_not_found",
            required=MembershipRole.MEMBER,
            for_update=True,
        )
        if not _can_delete(principal, project):
            raise ApiError(
                403,
                "insufficient_role",
                "Only the project's creator or a workspace admin can delete it",
            )
        if await _busy(session, project.id):
            raise ApiError(
                409,
                "project_busy",
                "A review or upload check is still running; stop it or wait, then try again",
            )
        intakes = (
            await session.scalars(select(Intake.id).where(Intake.project_id == project.id))
        ).all()
        snapshots = (
            await session.scalars(select(Snapshot.id).where(Snapshot.project_id == project.id))
        ).all()
        scans = (await session.scalars(select(Scan.id).where(Scan.project_id == project.id))).all()
        ai_runs = (
            await session.scalars(select(AiRun.id).where(AiRun.project_id == project.id))
        ).all()
        name = project.name
        await session.execute(delete(Project).where(Project.id == project.id))
    prefixes = [
        *(f"intakes/{i.hex}" for i in intakes),
        *(f"snapshots/{s.hex}" for s in snapshots),
        *(f"scans/{s.hex}" for s in scans),
        *(f"ai-runs/{r.hex}" for r in ai_runs),  # transcripts: as sensitive as the source
    ]
    artifacts = await asyncio.to_thread(_delete_prefixes, store, prefixes)
    blobs, deferred = await sweep_orphan_blobs(session_factory, store)
    logger.info(
        "project deleted",
        extra={
            "project_id": str(project_id),
            "project_name": name,
            "user_id": str(principal.user_id),
            "artifacts_deleted": artifacts,
            "blobs_deleted": blobs,
            "blob_sweep_deferred": deferred,
        },
    )
    return DeletionResult(project_id, artifacts, blobs, deferred)


async def sweep_orphan_blobs(
    session_factory: async_sessionmaker[AsyncSession], store: ArtifactStore
) -> tuple[int, bool]:
    """Delete ``blobs/`` no file entry references. Returns (deleted, deferred).

    Deferred when an intake is validating (it may be reusing an existing blob); the next project
    deletion or service start sweeps again. Never deletes a blob that any snapshot still uses.
    """
    async with transaction(session_factory) as session:
        await session.execute(text("LOCK TABLE intakes IN SHARE MODE"))
        validating = await session.scalar(
            select(func.count()).select_from(Intake).where(Intake.state.in_(BUSY_INTAKE_STATES))
        )
        if validating:
            return 0, True
        keys = await asyncio.to_thread(store.list_keys, "blobs")
        deleted = 0
        for start in range(0, len(keys), _BLOB_BATCH):
            batch = {key.segments[-1]: key for key in keys[start : start + _BLOB_BATCH]}
            referenced = set(
                (
                    await session.scalars(
                        select(FileEntry.blob_sha256)
                        .where(FileEntry.blob_sha256.in_(list(batch)))
                        .distinct()
                    )
                ).all()
            )
            orphans = [key for sha, key in batch.items() if sha not in referenced]
            deleted += await asyncio.to_thread(_delete_keys, store, orphans)
        return deleted, False
