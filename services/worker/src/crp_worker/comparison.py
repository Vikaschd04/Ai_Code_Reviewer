"""Finding comparison between two scans, shared by code reviews (P06) and fix-workspace checks
(P08). A base finding is "fixed" only on a compatible verified absence (P02 rules); renamed files
keep lineage through evidence-line matching."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from crp_analysis.lifecycle import (
    Prior,
    RunView,
    anchor_key,
    classify_absence,
    observed_ruleset,
    rule_hashes,
)
from crp_analysis.manifest import blob_key
from crp_analysis.normalize import evidence_line
from crp_analysis.sources.changes import FindingRef
from crp_core.artifacts import ArtifactKey, ArtifactStore
from crp_core.db.models import (
    EngineRun,
    FileCoverage,
    FileEntry,
    Finding,
    GraphBuild,
    GraphEdge,
    GraphNode,
    Scan,
)
from crp_core.domain.states import FileDisposition, RecheckState
from crp_core.workflows.contracts import EXTRACTOR_NAMES

RENAME_TEXT_FILES = 200


async def engine_summary(session: AsyncSession, scan_id: UUID) -> tuple[list[str], dict[str, int]]:
    """Checks that did not complete, and per-file result reuse of a scan."""
    runs = list(
        (await session.execute(select(EngineRun).where(EngineRun.scan_id == scan_id))).scalars()
    )
    incomplete = sorted(
        r.engine
        for r in runs
        if r.engine not in EXTRACTOR_NAMES and r.state not in {"SUCCEEDED", "NOT_APPLICABLE"}
    )
    cache = {
        "hits": sum(r.cache_hits or 0 for r in runs),
        "misses": sum(r.cache_misses or 0 for r in runs),
    }
    return incomplete, cache


def item(finding: FindingRef) -> dict[str, Any]:
    return {
        "finding_id": finding.id,
        "severity": finding.severity,
        "title": finding.title,
        "path": finding.path,
        "line": finding.start_line,
        "engine": finding.engine,
        "rule_id": finding.rule_id,
    }


async def manifest(session: AsyncSession, snapshot_id: UUID | None) -> dict[str, str]:
    rows = await session.execute(
        select(FileEntry.path, FileEntry.blob_sha256).where(
            FileEntry.snapshot_id == snapshot_id,
            FileEntry.disposition == FileDisposition.ANALYZABLE.value,
        )
    )
    return {path: sha for path, sha in rows.all() if sha}


async def dependencies(session: AsyncSession, snapshot_id: UUID | None) -> list[tuple[str, str]]:
    source_node, target_node = aliased(GraphNode), aliased(GraphNode)
    source_file, target_file = aliased(FileEntry), aliased(FileEntry)
    rows = await session.execute(
        select(source_file.path, target_file.path)
        .select_from(GraphEdge)
        .join(
            GraphBuild,
            and_(GraphBuild.id == GraphEdge.build_id, GraphBuild.is_current.is_(True)),
        )
        .join(source_node, source_node.id == GraphEdge.source_node_id)
        .join(target_node, target_node.id == GraphEdge.target_node_id)
        .join(source_file, source_file.id == source_node.file_entry_id)
        .join(target_file, target_file.id == target_node.file_entry_id)
        .where(GraphEdge.snapshot_id == snapshot_id, source_file.path != target_file.path)
        .distinct()
        .limit(50_000)
    )
    return [(a, b) for a, b in rows.all()]


async def findings_of(session: AsyncSession, scan_id: UUID) -> list[FindingRef]:
    rows = await session.execute(
        select(Finding, FileEntry.path)
        .join(FileEntry, FileEntry.id == Finding.file_entry_id)
        .where(Finding.scan_id == scan_id)
    )
    return [
        FindingRef(
            str(f.id),
            f.fingerprint,
            f.engine,
            f.rule_id,
            path,
            f.start_line,
            f.severity,
            f.title,
            rule_sha256=observed_ruleset(f.details, None),
            anchor_key=anchor_key(f.details),
        )
        for f, path in rows.all()
    ]


async def with_text(
    session: AsyncSession,
    store: ArtifactStore,
    max_bytes: int,
    findings: list[FindingRef],
    paths: set[str],
    snapshot_id: UUID | None,
) -> list[FindingRef]:
    """Add evidence-line text to findings in renamed files (for rename-aware matching)."""
    wanted = sorted({f.path for f in findings if f.path in paths and f.start_line})[
        :RENAME_TEXT_FILES
    ]
    if not wanted:
        return findings
    blobs = dict(
        (
            await session.execute(
                select(FileEntry.path, FileEntry.blob_sha256).where(
                    FileEntry.snapshot_id == snapshot_id, FileEntry.path.in_(wanted)
                )
            )
        ).all()
    )
    texts: dict[str, str] = {}
    for path, sha in blobs.items():
        if sha:
            data = await asyncio.to_thread(
                store.read_bytes, ArtifactKey(blob_key(sha)), max_bytes=max_bytes
            )
            texts[path] = data.decode("utf-8", errors="replace")
    return [
        FindingRef(
            f.id,
            f.fingerprint,
            f.engine,
            f.rule_id,
            f.path,
            f.start_line,
            f.severity,
            f.title,
            evidence_line(texts[f.path], f.start_line)
            if f.path in texts and f.start_line
            else None,
            f.rule_sha256,
            f.anchor_key,
        )
        for f in findings
    ]


async def classify_absent(
    session: AsyncSession,
    absent: tuple[FindingRef, ...],
    head: Scan,
    base: Scan | None,
    renames: dict[str, str],
) -> tuple[list[FindingRef], int]:
    if not absent or base is None:
        return [], len(absent)
    head_runs = {
        r.engine: r
        for r in (
            await session.execute(select(EngineRun).where(EngineRun.scan_id == head.id))
        ).scalars()
    }
    base_runs = {
        r.engine: r
        for r in (
            await session.execute(select(EngineRun).where(EngineRun.scan_id == base.id))
        ).scalars()
    }
    entries = dict(
        (
            await session.execute(
                select(FileEntry.path, FileEntry.id).where(
                    FileEntry.snapshot_id == head.snapshot_id,
                    FileEntry.disposition == FileDisposition.ANALYZABLE.value,
                )
            )
        ).all()
    )
    coverage = {
        (run_id, entry_id): outcome
        for run_id, entry_id, outcome in (
            await session.execute(
                select(
                    FileCoverage.engine_run_id, FileCoverage.file_entry_id, FileCoverage.outcome
                ).where(FileCoverage.engine_run_id.in_([r.id for r in head_runs.values()]))
            )
        ).all()
    }
    moved_to = {old: new for new, old in renames.items()}
    fixed: list[FindingRef] = []
    for finding in absent:
        path = moved_to.get(finding.path, finding.path)
        run = head_runs.get(finding.engine)
        prior_run = base_runs.get(finding.engine)
        entry_id = entries.get(path)
        view = (
            RunView(
                finding.engine,
                run.state,
                run.engine_version,
                run.ruleset_sha256,
                frozenset(run.enabled_rules) if run.enabled_rules is not None else None,
                rule_hashes(run.diagnostics),
            )
            if run is not None
            else None
        )
        verdict = classify_absence(
            Prior(
                finding.engine,
                finding.rule_id,
                path,
                prior_run.engine_version if prior_run else None,
                finding.rule_sha256 or (prior_run.ruleset_sha256 if prior_run else None),
            ),
            view,
            file_present=entry_id is not None,
            file_outcome=coverage.get((run.id, entry_id)) if run is not None and entry_id else None,
        )
        if verdict.state is RecheckState.VERIFIED_ABSENT:
            fixed.append(finding)
    return fixed, len(absent) - len(fixed)
