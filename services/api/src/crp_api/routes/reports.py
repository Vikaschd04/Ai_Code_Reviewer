"""Scan exports (JSON with the platform schema, SARIF 2.1.0) and scan-to-scan comparison.

Both require read access to the scan's project. Comparison classifies findings absent from the
target scan with the same strict rules as the issue lifecycle: absence is verified only for a
compatible, completed engine run that analyzed the file.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from crp_analysis.lifecycle import (
    Prior,
    RunView,
    classify_absence,
    observed_ruleset,
    rule_hashes,
)
from crp_analysis.reports import EXPORT_FORMAT, build_sarif
from crp_api import __version__
from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import (
    ComparisonGroup,
    ComparisonItem,
    EngineCompatibility,
    ScanComparison,
)
from crp_api.services.scope import get_scoped
from crp_core.db.models import (
    EngineRun,
    FileCoverage,
    FileEntry,
    Finding,
    Issue,
    Project,
    Scan,
    Snapshot,
)
from crp_core.db.session import transaction
from crp_core.domain.states import FileDisposition, RecheckState, ScanState, Severity
from crp_core.workflows.contracts import ENGINE_NAMES, EXTRACTOR_NAMES

router = APIRouter(tags=["reports"], responses={401: {"model": ErrorResponse}})
_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
}
GROUP_LIMIT = 200
_RANK = {s.value: i for i, s in enumerate(Severity)}
_ORDER = {name: i for i, name in enumerate(ENGINE_NAMES)}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def build_export(session: Any, scan: Scan) -> dict[str, Any]:
    project = await session.get(Project, scan.project_id)
    snapshot = await session.get(Snapshot, scan.snapshot_id)
    if project is None or snapshot is None:
        raise ApiError(404, "scan_not_found", "Scan not found")
    runs = sorted(
        (await session.execute(select(EngineRun).where(EngineRun.scan_id == scan.id))).scalars(),
        key=lambda r: _ORDER.get(r.engine, 99),
    )
    coverage: dict[uuid.UUID, Counter[str]] = {r.id: Counter() for r in runs}
    for run_id, outcome, count in (
        await session.execute(
            select(FileCoverage.engine_run_id, FileCoverage.outcome, func.count())
            .where(FileCoverage.engine_run_id.in_([r.id for r in runs]))
            .group_by(FileCoverage.engine_run_id, FileCoverage.outcome)
        )
    ).all():
        coverage[run_id][outcome] = count
    rows = (
        await session.execute(
            select(Finding, FileEntry.path, Issue)
            .join(FileEntry, FileEntry.id == Finding.file_entry_id)
            .outerjoin(Issue, Issue.id == Finding.issue_id)
            .where(Finding.scan_id == scan.id)
            .order_by(FileEntry.path, Finding.start_line, Finding.id)
        )
    ).all()
    recorded = (scan.summary or {}).get("limitations")
    limitations = [str(x) for x in recorded if x] if isinstance(recorded, list) else []
    limitations = limitations or [
        "Source-only analysis: no build, type resolution or runtime verification."
    ]
    return {
        "format": EXPORT_FORMAT,
        "generated_at": datetime.now(UTC).isoformat(),
        "tool": {"name": "refactorX", "version": __version__},
        "project": {"id": str(project.id), "slug": project.slug, "name": project.name},
        "snapshot": {
            "id": str(snapshot.id),
            "manifest_sha256": snapshot.manifest_sha256,
            "manifest_version": snapshot.manifest_version,
            "file_count": snapshot.file_count,
            "analyzable_count": snapshot.analyzable_count,
            "excluded_count": snapshot.excluded_count,
            "git_commit": snapshot.git_commit,
            "frozen_at": _iso(snapshot.frozen_at),
            "policy_version": snapshot.policy_version,
        },
        "scan": {
            "id": str(scan.id),
            "state": scan.state,
            "mode": scan.mode,
            "policy_version": scan.policy_version,
            "cache_mode": scan.cache_mode,
            "created_at": scan.created_at.isoformat(),
            "started_at": _iso(scan.started_at),
            "finished_at": _iso(scan.finished_at),
            "lifecycle_applied": scan.lifecycle_applied,
            "summary": scan.summary,
        },
        "engines": [
            {
                "engine": r.engine,
                "version": r.engine_version,
                "ruleset_id": r.ruleset_id,
                "ruleset_sha256": r.ruleset_sha256,
                "state": r.state,
                "files_eligible": r.files_eligible,
                "files_attempted": r.files_attempted,
                "files_succeeded": r.files_succeeded,
                "files_failed": r.files_failed,
                "findings": r.findings_count,
                "coverage": {
                    k: coverage[r.id].get(k, 0) for k in ("ANALYZED", "FAILED", "NOT_ATTEMPTED")
                },
                "cache_hits": r.cache_hits,
                "cache_misses": r.cache_misses,
                "duration_ms": r.duration_ms,
                "exit_code": r.exit_code,
                "error_code": r.error_code,
                "error_message": r.error_message,
                "raw_report_sha256": r.raw_artifact_sha256,
            }
            for r in runs
        ],
        "findings": [
            {
                "id": str(f.id),
                "fingerprint": f.fingerprint,
                "correlation_key": f.correlation_key,
                "engine": f.engine,
                "engine_version": f.engine_version,
                "rule_id": f.rule_id,
                "ruleset": f.ruleset,
                "rule_family": f.rule_family,
                "severity": f.severity,
                "engine_severity": f.engine_severity,
                "category": f.category,
                "confidence": f.confidence,
                "title": f.title,
                "message": f.message,
                "path": path,
                "anchor_kind": f.anchor_kind,
                "start_line": f.start_line,
                "start_column": f.start_column,
                "end_line": f.end_line,
                "end_column": f.end_column,
                "rule_url": f.rule_url,
                "details": f.details,
                "issue": {
                    "id": str(issue.id),
                    "status": issue.status,
                    "recheck_state": issue.recheck_state,
                }
                if issue is not None
                else None,
            }
            for f, path, issue in rows
        ],
        "limitations": limitations,
    }


@router.get(
    "/scans/{scan_id}/export",
    responses={**_ERRORS, 409: {"model": ErrorResponse}},
    response_class=JSONResponse,
)
async def export_scan(
    scan_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    format: Annotated[Literal["json", "sarif"], Query()] = "json",
) -> JSONResponse:
    """Download the scan as the platform JSON export or as SARIF 2.1.0 (terminal scans only)."""
    async with transaction(container.session_factory) as session:
        scan = await get_scoped(session, principal, Scan, scan_id, not_found="scan_not_found")
        if not ScanState(scan.state).is_terminal:
            raise ApiError(409, "scan_not_finished", "Exports are available once the scan ends")
        export = await build_export(session, scan)
    document = build_sarif(export) if format == "sarif" else export
    suffix = "sarif" if format == "sarif" else "json"
    return JSONResponse(
        document,
        media_type="application/sarif+json" if format == "sarif" else "application/json",
        headers={
            "Content-Disposition": f'attachment; filename="crp-scan-{scan.id.hex[:12]}.{suffix}"',
            "Cache-Control": "no-store",
        },
    )


def _group(items: list[ComparisonItem]) -> ComparisonGroup:
    items.sort(key=lambda i: (_RANK.get(i.severity, 9), i.path, i.rule_id))
    return ComparisonGroup(
        count=len(items), items=items[:GROUP_LIMIT], truncated=len(items) > GROUP_LIMIT
    )


@router.get(
    "/scans/{scan_id}/compare",
    response_model=ScanComparison,
    responses={**_ERRORS, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def compare_scans(
    scan_id: uuid.UUID,
    base: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
) -> ScanComparison:
    """Compare a target scan with a base scan of the same project (any snapshots)."""
    async with transaction(container.session_factory) as session:
        target = await get_scoped(session, principal, Scan, scan_id, not_found="scan_not_found")
        base_scan = await get_scoped(session, principal, Scan, base, not_found="scan_not_found")
        if base_scan.project_id != target.project_id:
            raise ApiError(422, "different_projects", "Both scans must belong to one project")
        for scan in (target, base_scan):
            if not ScanState(scan.state).is_terminal:
                raise ApiError(409, "scan_not_finished", "Both scans must have finished")
        findings: dict[uuid.UUID, dict[str, tuple[Finding, str]]] = {}
        runs: dict[uuid.UUID, dict[str, EngineRun]] = {}
        for scan in (target, base_scan):
            findings[scan.id] = {
                f.fingerprint: (f, path)
                for f, path in (
                    await session.execute(
                        select(Finding, FileEntry.path)
                        .join(FileEntry, FileEntry.id == Finding.file_entry_id)
                        .where(Finding.scan_id == scan.id)
                    )
                ).all()
            }
            runs[scan.id] = {
                r.engine: r
                for r in (
                    await session.execute(select(EngineRun).where(EngineRun.scan_id == scan.id))
                ).scalars()
                if r.engine not in EXTRACTOR_NAMES
            }
        entries = dict(
            (
                await session.execute(
                    select(FileEntry.path, FileEntry.id).where(
                        FileEntry.snapshot_id == target.snapshot_id,
                        FileEntry.disposition == FileDisposition.ANALYZABLE.value,
                    )
                )
            ).all()
        )
        target_runs = runs[target.id]
        coverage = {
            (run_id, entry_id): outcome
            for run_id, entry_id, outcome in (
                await session.execute(
                    select(
                        FileCoverage.engine_run_id,
                        FileCoverage.file_entry_id,
                        FileCoverage.outcome,
                    ).where(FileCoverage.engine_run_id.in_([r.id for r in target_runs.values()]))
                )
            ).all()
        }
    base_findings, target_findings = findings[base_scan.id], findings[target.id]
    groups: dict[str, list[ComparisonItem]] = {
        name: [] for name in ("new", "unchanged", *(s.value.lower() for s in RecheckState))
    }
    for fp, (f, path) in target_findings.items():
        other = base_findings.get(fp)
        groups["unchanged" if other else "new"].append(
            ComparisonItem(
                fingerprint=fp,
                engine=f.engine,
                rule_id=f.rule_id,
                path=path,
                title=f.title,
                severity=f.severity,
                finding_id=f.id,
                base_finding_id=other[0].id if other else None,
            )
        )
    views = {
        engine: RunView(
            engine,
            r.state,
            r.engine_version,
            r.ruleset_sha256,
            frozenset(r.enabled_rules) if r.enabled_rules is not None else None,
            rule_hashes(r.diagnostics),
        )
        for engine, r in target_runs.items()
    }
    for fp, (f, path) in base_findings.items():
        if fp in target_findings:
            continue
        base_run = runs[base_scan.id].get(f.engine)
        target_run = target_runs.get(f.engine)
        entry_id = entries.get(path)
        result = classify_absence(
            Prior(
                f.engine,
                f.rule_id,
                path,
                f.engine_version,
                observed_ruleset(f.details, base_run.ruleset_sha256 if base_run else None),
            ),
            views.get(f.engine),
            file_present=entry_id is not None,
            file_outcome=coverage.get((target_run.id, entry_id))
            if target_run and entry_id
            else None,
        )
        groups[result.state.value.lower()].append(
            ComparisonItem(
                fingerprint=fp,
                engine=f.engine,
                rule_id=f.rule_id,
                path=path,
                title=f.title,
                severity=f.severity,
                finding_id=None,
                base_finding_id=f.id,
                reason=result.reason,
            )
        )
    engines = []
    for name in sorted(set(runs[base_scan.id]) | set(target_runs), key=lambda e: _ORDER.get(e, 99)):
        b, t = runs[base_scan.id].get(name), target_runs.get(name)
        same_rules = bool(b and t and b.ruleset_sha256 == t.ruleset_sha256)
        completed = bool(
            b and t and b.state in {"SUCCEEDED", "PARTIAL"} and t.state in {"SUCCEEDED", "PARTIAL"}
        )
        compatible = (
            completed and same_rules and bool(b and t and b.engine_version == t.engine_version)
        )
        if compatible:
            note = "compatible: absences in analyzed files are verified"
        elif b and t and b.state == t.state == "NOT_APPLICABLE":
            note = "not applicable: no files for this check on either side"
        elif not completed:
            note = "one side did not complete; absences are not rechecked"
        else:
            note = "engine version or rules differ; absences are unknown"
        engines.append(
            EngineCompatibility(
                engine=name,
                base_state=b.state if b else None,
                target_state=t.state if t else None,
                base_version=b.engine_version if b else None,
                target_version=t.engine_version if t else None,
                same_rules=same_rules,
                compatible=compatible,
                note=note,
            )
        )
    notes = [
        "Findings are matched by fingerprint (engine, rule, path, normalized evidence line, "
        "occurrence); an edited evidence line appears as one absent and one new finding.",
        "An absent finding counts as fixed only when verified absent.",
    ]
    if base_scan.snapshot_id == target.snapshot_id:
        notes.append("Both scans analyzed the same snapshot; differences come from engines/rules.")
    return ScanComparison(
        base_scan_id=base_scan.id,
        target_scan_id=target.id,
        base_snapshot_id=base_scan.snapshot_id,
        target_snapshot_id=target.snapshot_id,
        new=_group(groups["new"]),
        unchanged=_group(groups["unchanged"]),
        verified_absent=_group(groups["verified_absent"]),
        not_rechecked=_group(groups["not_rechecked"]),
        unknown=_group(groups["unknown"]),
        rule_obsolete=_group(groups["rule_obsolete"]),
        engines=engines,
        notes=notes,
    )
