"""Inputs of a project's NFR checkpoints (ADR 0024): the newest reviewed upload's files, declared
libraries, engine runs and configuration evidence (engine ``nfr``), the project's open issues,
and the team's newest checkpoint decisions."""

from __future__ import annotations

import posixpath
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from crp_analysis.catalog import lookup
from crp_analysis.insights.decisions import handled_from
from crp_analysis.insights.engine import (
    OPEN,
    Report,
    ReviewContext,
    TrackedIssue,
    build,
    review_context,
)
from crp_analysis.nfr.signals import ConfigSignals, Evidence, LibraryUse, detect
from crp_core.db.models import (
    EngineRun,
    FileEntry,
    GraphBuild,
    GraphEdge,
    Issue,
    NfrProfileVersion,
    Scan,
)
from crp_core.domain.states import EngineState, FileDisposition, GraphBuildState

_ECOSYSTEM = {"pom.xml": "maven", "package.json": "npm"}
_CONFIG_RAN = frozenset(
    {EngineState.SUCCEEDED.value, EngineState.PARTIAL.value, EngineState.NOT_APPLICABLE.value}
)


@dataclass(slots=True)
class Basis:
    snapshot_id: uuid.UUID
    scan_id: uuid.UUID
    reviewed_at: datetime | None


async def basis(session: Any, project_id: uuid.UUID) -> Basis | None:
    """The newest upload whose review updated the project's issues."""
    scan = (
        await session.execute(
            select(Scan)
            .where(Scan.project_id == project_id, Scan.lifecycle_applied.is_(True))
            .order_by(Scan.finished_at.desc().nulls_last())
            .limit(1)
        )
    ).scalar_one_or_none()
    return Basis(scan.snapshot_id, scan.id, scan.finished_at) if scan else None


async def paths(session: Any, snapshot_id: uuid.UUID) -> list[str]:
    rows = await session.execute(
        select(FileEntry.path).where(
            FileEntry.snapshot_id == snapshot_id,
            FileEntry.disposition == FileDisposition.ANALYZABLE.value,
        )
    )
    return [path for (path,) in rows.all()]


async def libraries(session: Any, snapshot_id: uuid.UUID) -> list[LibraryUse]:
    """Dependencies declared in the manifests, from the snapshot's current dependency map."""
    build_row = (
        await session.execute(
            select(GraphBuild).where(
                GraphBuild.snapshot_id == snapshot_id, GraphBuild.is_current.is_(True)
            )
        )
    ).scalar_one_or_none()
    if build_row is None or build_row.state == GraphBuildState.FAILED.value:
        return []
    rows = await session.execute(
        select(GraphEdge.target_ref, FileEntry.path, GraphEdge.evidence_start_line)
        .join(FileEntry, FileEntry.id == GraphEdge.evidence_file_entry_id)
        .where(GraphEdge.build_id == build_row.id, GraphEdge.relation == "depends_on")
    )
    uses: list[LibraryUse] = []
    for name, path, line in rows.all():
        ecosystem = _ECOSYSTEM.get(posixpath.basename(path))
        if ecosystem is not None:
            uses.append(LibraryUse(ecosystem, name, path, line))
    return uses


async def runs(session: Any, scan_id: uuid.UUID) -> dict[str, tuple[str, dict[str, object] | None]]:
    """Engine -> (state, diagnostics) of the review."""
    rows = await session.execute(
        select(EngineRun.engine, EngineRun.state, EngineRun.diagnostics).where(
            EngineRun.scan_id == scan_id
        )
    )
    return {engine: (state, diagnostics) for engine, state, diagnostics in rows.all()}


def config_signals(
    engine_runs: dict[str, tuple[str, dict[str, object] | None]],
) -> ConfigSignals | None:
    """What the configuration checks found, or None when they did not run or complete."""
    state, diagnostics = engine_runs.get("nfr", ("", None))
    if state not in _CONFIG_RAN:
        return None
    signals = (diagnostics or {}).get("signals")
    return [s for s in signals if isinstance(s, dict)] if isinstance(signals, list) else []


async def issues(session: Any, project_id: uuid.UUID) -> list[TrackedIssue]:
    rows = (
        await session.execute(
            select(Issue).where(Issue.project_id == project_id, Issue.status.in_(OPEN))
        )
    ).scalars()
    return [
        TrackedIssue(
            str(i.id),
            i.engine,
            i.rule_id,
            i.category,
            lookup(i.engine, i.rule_id, None, None).family,
            i.severity,
            i.title,
            i.path,
            i.status,
        )
        for i in rows
    ]


async def decisions(session: Any, project_id: uuid.UUID) -> tuple[int, dict[str, str]]:
    """The newest decisions version (0 when none) and its "handled elsewhere" reasons."""
    latest = (
        await session.execute(
            select(NfrProfileVersion)
            .where(NfrProfileVersion.project_id == project_id)
            .order_by(NfrProfileVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None:
        return 0, {}
    return latest.version, handled_from(latest.document)


@dataclass(slots=True)
class Gathered:
    """Everything one project's checkpoints are computed from."""

    basis: Basis | None
    evidence: list[Evidence]
    issues: list[TrackedIssue]
    context: ReviewContext
    version: int
    handled: dict[str, str]

    def report(self) -> Report:
        return build(self.issues, self.evidence, self.context, self.handled)


async def gather(session: Any, project_id: uuid.UUID) -> Gathered:
    found = await basis(session, project_id)
    evidence: list[Evidence] = []
    context = ReviewContext(reviewed=False)
    if found is not None:
        engine_runs = await runs(session, found.scan_id)
        files = await paths(session, found.snapshot_id)
        uses = await libraries(session, found.snapshot_id)
        evidence = detect(files, uses, config_signals(engine_runs))
        context = review_context(engine_runs, uses, files)
    version, handled = await decisions(session, project_id)
    return Gathered(found, evidence, await issues(session, project_id), context, version, handled)
