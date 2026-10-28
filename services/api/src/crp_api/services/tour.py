"""Gather a project's code tour (ADD-A7) from its newest reviewed upload: the inventory, the
dependency map, ``main`` symbols, the project's open issues and the upload's files."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crp_analysis.architecture.tour import Tour, build_tour
from crp_analysis.insights.engine import OPEN
from crp_api.services import architecture, insights
from crp_core.db.models import CodeSymbol, FileEntry, GraphBuild, Issue, Snapshot
from crp_core.domain.states import FileDisposition, GraphBuildState


async def gather(session: AsyncSession, project_id: uuid.UUID) -> tuple[uuid.UUID, Tour] | None:
    """(snapshot id, tour) of the newest reviewed upload, or None before the first review."""
    found = await insights.basis(session, project_id)
    if found is None:
        return None
    snapshot = await session.get(Snapshot, found.snapshot_id)
    build = (
        await session.execute(
            select(GraphBuild).where(
                GraphBuild.snapshot_id == found.snapshot_id, GraphBuild.is_current.is_(True)
            )
        )
    ).scalar_one_or_none()
    model = (
        await architecture.load_model(session, build)
        if build is not None and build.state != GraphBuildState.FAILED.value
        else None
    )
    symbols = await session.execute(
        select(FileEntry.path, CodeSymbol.kind, CodeSymbol.name)
        .join(FileEntry, FileEntry.id == CodeSymbol.file_entry_id)
        .where(CodeSymbol.snapshot_id == found.snapshot_id, CodeSymbol.name == "main")
    )
    issues = await session.execute(
        select(Issue.path, Issue.severity).where(
            Issue.project_id == project_id, Issue.status.in_(OPEN)
        )
    )
    paths = await session.scalars(
        select(FileEntry.path).where(
            FileEntry.snapshot_id == found.snapshot_id,
            FileEntry.disposition == FileDisposition.ANALYZABLE.value,
        )
    )
    tour = build_tour(
        model,
        (snapshot.inventory or {}) if snapshot else {},
        [(p, k, n) for p, k, n in symbols.all()],
        [(p, s) for p, s in issues.all()],
        list(paths.all()),
    )
    return found.snapshot_id, tour
