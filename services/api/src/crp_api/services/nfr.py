"""Inputs of a project's NFR assessment (P12): the newest reviewed upload's files, declared
libraries and configuration evidence (engine ``nfr``), the project's tracked issues, and the
newest NFR profile version."""

from __future__ import annotations

import posixpath
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from crp_analysis.catalog import lookup
from crp_analysis.nfr.assessment import ACCEPTED, OPEN, Assessment, TrackedIssue, assess
from crp_analysis.nfr.profile import Profile, from_document
from crp_analysis.nfr.questionnaire import load
from crp_analysis.nfr.signals import ConfigSignals, Evidence, LibraryUse, detect
from crp_core.db.models import (
    EngineRun,
    FileEntry,
    GraphBuild,
    GraphEdge,
    Issue,
    NfrProfileVersion,
    Scan,
    User,
)
from crp_core.domain.states import EngineState, FileDisposition, GraphBuildState

_ECOSYSTEM = {"pom.xml": "maven", "package.json": "npm"}


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
    build = (
        await session.execute(
            select(GraphBuild).where(
                GraphBuild.snapshot_id == snapshot_id, GraphBuild.is_current.is_(True)
            )
        )
    ).scalar_one_or_none()
    if build is None or build.state == GraphBuildState.FAILED.value:
        return []
    rows = await session.execute(
        select(GraphEdge.target_ref, FileEntry.path, GraphEdge.evidence_start_line)
        .join(FileEntry, FileEntry.id == GraphEdge.evidence_file_entry_id)
        .where(GraphEdge.build_id == build.id, GraphEdge.relation == "depends_on")
    )
    uses: list[LibraryUse] = []
    for name, path, line in rows.all():
        ecosystem = _ECOSYSTEM.get(posixpath.basename(path))
        if ecosystem is not None:
            uses.append(LibraryUse(ecosystem, name, path, line))
    return uses


_CONFIG_RAN = frozenset(
    {EngineState.SUCCEEDED.value, EngineState.PARTIAL.value, EngineState.NOT_APPLICABLE.value}
)


async def config_signals(session: Any, scan_id: uuid.UUID) -> ConfigSignals | None:
    """What the configuration checks found in the review, or None when they did not run (older
    reviews) or did not complete: then the assessment says the configuration is not checked."""
    run = (
        await session.execute(
            select(EngineRun).where(EngineRun.scan_id == scan_id, EngineRun.engine == "nfr")
        )
    ).scalar_one_or_none()
    if run is None or run.state not in _CONFIG_RAN:
        return None
    signals = (run.diagnostics or {}).get("signals")
    return [s for s in signals if isinstance(s, dict)] if isinstance(signals, list) else []


async def issues(session: Any, project_id: uuid.UUID) -> list[TrackedIssue]:
    rows = (
        await session.execute(
            select(Issue).where(Issue.project_id == project_id, Issue.status.in_([*OPEN, ACCEPTED]))
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


async def history(
    session: Any, project_id: uuid.UUID, limit: int = 50
) -> list[tuple[NfrProfileVersion, str | None]]:
    rows = await session.execute(
        select(NfrProfileVersion, User.display_name)
        .outerjoin(User, User.id == NfrProfileVersion.created_by)
        .where(NfrProfileVersion.project_id == project_id)
        .order_by(NfrProfileVersion.version.desc())
        .limit(limit)
    )
    return [(version, name) for version, name in rows.all()]


@dataclass(slots=True)
class Gathered:
    """Everything one project's NFR and insight views are computed from."""

    versions: list[tuple[NfrProfileVersion, str | None]]
    profile: Profile
    basis: Basis | None
    evidence: list[Evidence]
    issues: list[TrackedIssue]
    assessment: Assessment


async def gather(session: Any, project_id: uuid.UUID) -> Gathered:
    versions = await history(session, project_id)
    profile = from_document(versions[0][0].document) if versions else Profile()
    found = await basis(session, project_id)
    evidence: list[Evidence] = []
    if found is not None:
        evidence = detect(
            await paths(session, found.snapshot_id),
            await libraries(session, found.snapshot_id),
            await config_signals(session, found.scan_id),
        )
    tracked = await issues(session, project_id)
    assessment = assess(load(), evidence, tracked, profile, reviewed=found is not None)
    return Gathered(versions, profile, found, evidence, tracked, assessment)
