"""Scans: idempotent creation, status with engine outcomes, SSE progress, cancellation, coverage,
findings and finding detail with exact source evidence."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert

from crp_analysis.catalog import lookup
from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.routes.snapshots import file_content
from crp_api.schemas import (
    CorrelatedFinding,
    CoveragePage,
    CoverageRow,
    EngineRunResponse,
    FindingDetailResponse,
    FindingPage,
    FindingResponse,
    IssueRef,
    RuleGuidance,
    ScanCreate,
    ScanPage,
    ScanResponse,
)
from crp_api.services.scope import decode_offset, encode_offset, get_scoped
from crp_core.db.models import (
    EngineRun,
    FileCoverage,
    FileEntry,
    Finding,
    Issue,
    Project,
    Scan,
    ScanEvent,
    Snapshot,
)
from crp_core.db.session import transaction
from crp_core.domain.states import (
    AnchorKind,
    CaptureStatus,
    MembershipRole,
    ScanState,
    Severity,
)
from crp_core.workflows.contracts import ENGINE_NAMES
from crp_core.workflows.gateway import WorkflowUnavailableError

router = APIRouter(tags=["scans"], responses={401: {"model": ErrorResponse}})
_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
}
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
SCAN_MODE = "baseline"
POLICY = "crp-baseline-v1"
_SEVERITY_ORDER = {s.value: i for i, s in enumerate(Severity)}
_EVENT_STREAM_SECONDS = 1800


def _engine(run: EngineRun) -> EngineRunResponse:
    return EngineRunResponse(
        engine=run.engine,
        engine_version=run.engine_version,
        ruleset_id=run.ruleset_id,
        ruleset_sha256=run.ruleset_sha256,
        state=run.state,
        files_eligible=run.files_eligible,
        files_attempted=run.files_attempted,
        files_succeeded=run.files_succeeded,
        files_failed=run.files_failed,
        findings_count=run.findings_count,
        exit_code=run.exit_code,
        duration_ms=run.duration_ms,
        error_code=run.error_code,
        error_message=run.error_message,
        diagnostics=run.diagnostics,
        enabled_rule_count=len(run.enabled_rules) if run.enabled_rules is not None else None,
        cache_hits=run.cache_hits,
        cache_misses=run.cache_misses,
        raw_artifact_sha256=run.raw_artifact_sha256,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


async def _scan_response(session: Any, scan: Scan) -> ScanResponse:
    runs = (await session.execute(select(EngineRun).where(EngineRun.scan_id == scan.id))).scalars()
    order = {name: i for i, name in enumerate(ENGINE_NAMES)}
    snapshot = await session.get(Snapshot, scan.snapshot_id)
    return ScanResponse(
        id=scan.id,
        project_id=scan.project_id,
        snapshot_id=scan.snapshot_id,
        cache_mode=scan.cache_mode,
        lifecycle_applied=scan.lifecycle_applied,
        manifest_sha256=snapshot.manifest_sha256 if snapshot else None,
        state=scan.state,
        mode=scan.mode,
        policy_version=scan.policy_version,
        created_at=scan.created_at,
        started_at=scan.started_at,
        finished_at=scan.finished_at,
        cancel_requested_at=scan.cancel_requested_at,
        error_code=scan.error_code,
        error_message=scan.error_message,
        engines=[_engine(r) for r in sorted(runs, key=lambda r: order.get(r.engine, 9))],
        summary=scan.summary,
    )


@router.post(
    "/projects/{project_id}/scans",
    status_code=202,
    response_model=ScanResponse,
    responses={**_ERRORS, 409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def create_scan(
    project_id: uuid.UUID,
    body: ScanCreate,
    principal: CurrentPrincipal,
    container: Container,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ScanResponse:
    """Start a baseline scan of a frozen snapshot. Repeating a request with the same
    Idempotency-Key returns the existing scan instead of starting another one."""
    key = idempotency_key or uuid.uuid4().hex
    if not _IDEMPOTENCY.fullmatch(key):
        raise ApiError(
            400, "invalid_idempotency_key", "Idempotency-Key must be 8-128 safe characters"
        )
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session,
            principal,
            Project,
            project_id,
            not_found="project_not_found",
            required=MembershipRole.MEMBER,
        )
        snapshot = await session.get(Snapshot, body.snapshot_id)
        if snapshot is None or snapshot.project_id != project.id:
            raise ApiError(404, "snapshot_not_found", "Snapshot not found in this project")
        if snapshot.capture_status != CaptureStatus.FROZEN.value:
            raise ApiError(409, "snapshot_not_ready", "Only frozen snapshots can be scanned")
        scan_id = (
            await session.execute(
                insert(Scan)
                .values(
                    id=uuid.uuid4(),
                    workspace_id=project.workspace_id,
                    project_id=project.id,
                    snapshot_id=snapshot.id,
                    mode=SCAN_MODE,
                    policy_version=POLICY,
                    idempotency_key=key,
                    state=ScanState.QUEUED.value,
                    cache_mode=body.cache_mode,
                    requested_by=principal.user_id,
                )
                .on_conflict_do_nothing(index_elements=[Scan.project_id, Scan.idempotency_key])
                .returning(Scan.id)
            )
        ).scalar_one_or_none()
        if scan_id is None:
            existing = (
                await session.execute(
                    select(Scan).where(Scan.project_id == project.id, Scan.idempotency_key == key)
                )
            ).scalar_one()
            if existing.snapshot_id != snapshot.id:
                raise ApiError(
                    409,
                    "idempotency_conflict",
                    "This Idempotency-Key was used for a different snapshot",
                )
            scan_id = existing.id
        scan = await session.get(Scan, scan_id)
        if scan is None:
            raise ApiError(404, "scan_not_found", "Scan not found")
        scan.workflow_id = f"crp-scan-{scan.id.hex}"
        needs_start = scan.state == ScanState.QUEUED.value
        response = await _scan_response(session, scan)
    if needs_start:
        try:
            await container.workflows.start_scan(scan_id)
        except WorkflowUnavailableError as exc:
            raise ApiError(
                503, "workflow_unavailable", f"{exc}; retry with the same Idempotency-Key"
            ) from exc
    return response


@router.get("/projects/{project_id}/scans", response_model=ScanPage, responses=_ERRORS)
async def list_scans(
    project_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> ScanPage:
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        scans = list(
            (
                await session.execute(
                    select(Scan)
                    .where(Scan.project_id == project.id)
                    .order_by(Scan.created_at.desc())
                    .limit(25)
                )
            ).scalars()
        )
        return ScanPage(items=[await _scan_response(session, s) for s in scans])


@router.get("/scans/{scan_id}", response_model=ScanResponse, responses=_ERRORS)
async def get_scan(
    scan_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> ScanResponse:
    async with transaction(container.session_factory) as session:
        scan = await get_scoped(session, principal, Scan, scan_id, not_found="scan_not_found")
        return await _scan_response(session, scan)


@router.post(
    "/scans/{scan_id}/cancel",
    status_code=202,
    response_model=ScanResponse,
    responses={**_ERRORS, 503: {"model": ErrorResponse}},
)
async def cancel_scan(
    scan_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> ScanResponse:
    """Idempotent cancellation; completed engine results stay visible, the scan ends CANCELED."""
    async with transaction(container.session_factory) as session:
        scan = await get_scoped(
            session,
            principal,
            Scan,
            scan_id,
            not_found="scan_not_found",
            required=MembershipRole.MEMBER,
            for_update=True,
        )
        terminal = ScanState(scan.state).is_terminal
        if not terminal and scan.cancel_requested_at is None:
            scan.cancel_requested_at = datetime.now(UTC)
        response = await _scan_response(session, scan)
    if not terminal:
        try:
            await container.workflows.cancel_scan(scan_id)
        except WorkflowUnavailableError as exc:
            raise ApiError(503, "workflow_unavailable", str(exc)) from exc
    return response


@router.get(
    "/scans/{scan_id}/events",
    responses={**_ERRORS, 200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
    response_class=StreamingResponse,
)
async def scan_events(
    scan_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal,
    container: Container,
    after: Annotated[int | None, Query(ge=0)] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Server-sent events with monotonic IDs; reconnect with Last-Event-ID to resume."""
    async with transaction(container.session_factory) as session:
        await get_scoped(session, principal, Scan, scan_id, not_found="scan_not_found")
    cursor = after or (int(last_event_id) if last_event_id and last_event_id.isdigit() else 0)

    async def stream() -> AsyncIterator[str]:
        position = cursor
        started = time.monotonic()
        last_sent = started
        while time.monotonic() - started < _EVENT_STREAM_SECONDS:
            if await request.is_disconnected():
                return
            async with transaction(container.session_factory) as session:
                events = list(
                    (
                        await session.execute(
                            select(ScanEvent)
                            .where(ScanEvent.scan_id == scan_id, ScanEvent.id > position)
                            .order_by(ScanEvent.id)
                            .limit(100)
                        )
                    ).scalars()
                )
                state = (
                    await session.execute(select(Scan.state).where(Scan.id == scan_id))
                ).scalar_one()
            for event in events:
                position = event.id
                data = {**event.payload, "created_at": event.created_at.isoformat()}
                yield f"id: {event.id}\nevent: {event.kind}\ndata: {json.dumps(data)}\n\n"
                last_sent = time.monotonic()
            if not events and ScanState(state).is_terminal:
                yield f"event: end\ndata: {json.dumps({'state': state})}\n\n"
                return
            if time.monotonic() - last_sent > 15:
                yield ": keep-alive\n\n"
                last_sent = time.monotonic()
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/scans/{scan_id}/coverage", response_model=CoveragePage, responses=_ERRORS)
async def scan_coverage(
    scan_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    engine: Annotated[str | None, Query(max_length=32)] = None,
    outcome: Annotated[str | None, Query(max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
) -> CoveragePage:
    offset = decode_offset(cursor)
    async with transaction(container.session_factory) as session:
        scan = await get_scoped(session, principal, Scan, scan_id, not_found="scan_not_found")
        query = (
            select(FileCoverage, FileEntry.path, EngineRun.engine)
            .join(EngineRun, EngineRun.id == FileCoverage.engine_run_id)
            .join(FileEntry, FileEntry.id == FileCoverage.file_entry_id)
            .where(EngineRun.scan_id == scan.id)
        )
        if engine:
            query = query.where(EngineRun.engine == engine)
        if outcome:
            query = query.where(FileCoverage.outcome == outcome)
        rows = (
            await session.execute(
                query.order_by(FileEntry.path, EngineRun.engine).offset(offset).limit(limit + 1)
            )
        ).all()
    return CoveragePage(
        items=[
            CoverageRow(
                file_id=cov.file_entry_id,
                path=path,
                engine=eng,
                outcome=cov.outcome,
                reason=cov.reason,
                cached=cov.cached,
            )
            for cov, path, eng in rows[:limit]
        ],
        next_cursor=encode_offset(offset + limit) if len(rows) > limit else None,
    )


def _finding(
    row: Finding,
    path: str,
    issue: Issue | None = None,
    related: list[CorrelatedFinding] | None = None,
) -> FindingResponse:
    return FindingResponse(
        id=row.id,
        scan_id=row.scan_id,
        snapshot_id=row.snapshot_id,
        file_id=row.file_entry_id,
        path=path,
        engine=row.engine,
        engine_version=row.engine_version,
        rule_id=row.rule_id,
        ruleset=row.ruleset,
        severity=row.severity,
        category=row.category,
        confidence=row.confidence,
        title=row.title,
        message=row.message,
        anchor_kind=AnchorKind(row.anchor_kind).value,
        start_line=row.start_line,
        start_column=row.start_column,
        end_line=row.end_line,
        end_column=row.end_column,
        rule_url=row.rule_url,
        status=row.status,
        fingerprint=row.fingerprint,
        correlation_key=row.correlation_key,
        rule_family=row.rule_family,
        in_catalog=row.in_catalog,
        details=row.details,
        issue=IssueRef(
            id=issue.id,
            status=issue.status,
            recheck_state=issue.recheck_state,
            version=issue.version,
        )
        if issue is not None
        else None,
        also_reported_by=related or [],
    )


async def _correlated(
    session: Any, scan_id: uuid.UUID, findings: list[Finding]
) -> dict[uuid.UUID, list[CorrelatedFinding]]:
    """Other engines' findings with the same correlation key, per finding id."""
    keys = {f.correlation_key for f in findings}
    if not keys:
        return {}
    rows = (
        await session.execute(
            select(
                Finding.id,
                Finding.engine,
                Finding.rule_id,
                Finding.severity,
                Finding.correlation_key,
            ).where(Finding.scan_id == scan_id, Finding.correlation_key.in_(keys))
        )
    ).all()
    groups: dict[str, list[Any]] = {}
    for row in rows:
        groups.setdefault(row.correlation_key, []).append(row)
    return {
        f.id: [
            CorrelatedFinding(id=r.id, engine=r.engine, rule_id=r.rule_id, severity=r.severity)
            for r in groups.get(f.correlation_key, [])
            if r.id != f.id and r.engine != f.engine
        ]
        for f in findings
    }


@router.get("/scans/{scan_id}/findings", response_model=FindingPage, responses=_ERRORS)
async def list_findings(
    scan_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    severity: Annotated[list[str] | None, Query()] = None,
    category: Annotated[list[str] | None, Query()] = None,
    engine: Annotated[str | None, Query(max_length=32)] = None,
    rule_id: Annotated[str | None, Query(max_length=128)] = None,
    file_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    issue_status: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
) -> FindingPage:
    """Filtered findings ordered by severity, path and line (published results are immutable,
    so offset cursors are stable within a scan)."""
    offset = decode_offset(cursor)
    async with transaction(container.session_factory) as session:
        scan = await get_scoped(session, principal, Scan, scan_id, not_found="scan_not_found")
        query = (
            select(Finding, FileEntry.path, Issue)
            .join(FileEntry, FileEntry.id == Finding.file_entry_id)
            .outerjoin(Issue, Issue.id == Finding.issue_id)
            .where(Finding.scan_id == scan.id)
        )
        if issue_status:
            query = query.where(Issue.status.in_(issue_status))
        if severity:
            query = query.where(Finding.severity.in_(severity))
        if category:
            query = query.where(Finding.category.in_(category))
        if engine:
            query = query.where(Finding.engine == engine)
        if rule_id:
            query = query.where(Finding.rule_id == rule_id)
        if file_id:
            query = query.where(Finding.file_entry_id == file_id)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(
                FileEntry.path.ilike(f"%{escaped}%", escape="\\")
                | Finding.title.ilike(f"%{escaped}%", escape="\\")
            )
        total = (
            await session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rank = case(_SEVERITY_ORDER, value=Finding.severity, else_=9)
        rows = (
            await session.execute(
                query.order_by(rank, FileEntry.path, Finding.start_line, Finding.id)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        page = rows[:limit]
        related = await _correlated(session, scan.id, [f for f, _, _ in page])
    return FindingPage(
        items=[_finding(f, path, issue, related.get(f.id)) for f, path, issue in page],
        next_cursor=encode_offset(offset + limit) if len(rows) > limit else None,
        total=total,
    )


@router.get("/findings/{finding_id}", response_model=FindingDetailResponse, responses=_ERRORS)
async def get_finding(
    finding_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> FindingDetailResponse:
    async with transaction(container.session_factory) as session:
        finding = await get_scoped(
            session, principal, Finding, finding_id, not_found="finding_not_found"
        )
        entry = await session.get(FileEntry, finding.file_entry_id)
        snapshot = await session.get(Snapshot, finding.snapshot_id)
        issue = await session.get(Issue, finding.issue_id) if finding.issue_id else None
        related = (await _correlated(session, finding.scan_id, [finding])).get(finding.id, [])
        related_rows = (
            await session.execute(
                select(Finding, FileEntry.path, Issue)
                .join(FileEntry, FileEntry.id == Finding.file_entry_id)
                .outerjoin(Issue, Issue.id == Finding.issue_id)
                .where(Finding.id.in_([r.id for r in related]))
            )
        ).all()
    if entry is None:
        raise ApiError(404, "finding_not_found", "Finding not found")
    info = lookup(finding.engine, finding.rule_id, finding.engine_severity, finding.rule_url)
    guidance = finding.guidance or {}
    source = None
    if finding.start_line is not None and finding.end_line is not None:
        context_start = max(1, finding.start_line - 6)
        source = await file_content(container, entry, context_start, finding.end_line + 6)
    return FindingDetailResponse(
        finding=_finding(finding, entry.path, issue, related),
        rule=RuleGuidance(
            title=str(guidance.get("title") or info.title),
            explanation=str(guidance.get("explanation") or info.explanation),
            recommendation=str(guidance.get("recommendation") or info.recommendation),
            severity_rationale=str(guidance.get("severity_rationale") or info.severity_rationale),
            url=(str(guidance["url"]) if guidance.get("url") else None)
            or info.url
            or finding.rule_url,
            in_catalog=info.in_catalog or bool(guidance),
        ),
        source=source,
        related=[_finding(f, path, i) for f, path, i in related_rows],
        manifest_sha256=snapshot.manifest_sha256 if snapshot else None,
        evidence_note=(
            f"Reported by {finding.engine} {finding.engine_version} (trusted ruleset "
            f"{finding.ruleset or 'n/a'}) on the frozen snapshot; source-only analysis, not "
            "build- or runtime-verified."
        ),
    )
