"""Fix workspaces (P08): fix many issues by hand, with recipes or (later) AI, re-check, compare and
export. The upload stays untouched; every change keeps its provenance; exports name the exact
upload they apply to.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import uuid
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from email.header import Header
from email.utils import format_datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError
from starlette.background import BackgroundTask

from crp_analysis.ai.config import resolve
from crp_analysis.ai.prompts import FIX_PROMPT_VERSION
from crp_analysis.fixes import recipes
from crp_analysis.fixes.changeset import (
    EXPORT_FORMAT,
    apply_on_current,
    content_digest,
    edit_flags,
    file_patch,
)
from crp_analysis.fixes.patching import Edit, PatchError, apply_edits
from crp_analysis.manifest import blob_key
from crp_analysis.sources.changes import diff_manifests
from crp_api import __version__
from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.auth.principal import Principal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.routes.ai import ai_gate, run_limits, run_response
from crp_api.schemas import (
    AiRunPage,
    AiRunResponse,
    ChangeSetCheckResponse,
    ChangeSetCreate,
    ChangeSetEventResponse,
    ChangeSetFileSummary,
    ChangeSetList,
    ChangeSetListItem,
    ChangeSetResponse,
    FileComparison,
    SnapshotChange,
    SnapshotComparisonResponse,
    WorkspaceAiFixApply,
    WorkspaceAiFixRequest,
    WorkspaceAiStatus,
    WorkspaceFileContent,
    WorkspaceFilePath,
    WorkspaceFileSave,
    WorkspaceFixApplied,
    WorkspaceFixRequest,
    WorkspaceFixResult,
    WorkspaceFixSkipped,
    WorkspaceIssue,
    WorkspaceIssuePage,
    WorkspaceSaveResult,
)
from crp_api.services import change_sets as workspace
from crp_api.services.scope import decode_offset, encode_offset, get_scoped
from crp_api.services.snapshot_files import read_blob_text, sfdx_reader
from crp_core.artifacts import ArtifactKey
from crp_core.db.models import (
    AiRun,
    ChangeSet,
    ChangeSetCheck,
    ChangeSetEvent,
    ChangeSetFile,
    FileEntry,
    Finding,
    Project,
    ProjectAiPolicy,
    Scan,
    Snapshot,
    Source,
)
from crp_core.db.session import transaction
from crp_core.domain.states import (
    AiRunKind,
    AiRunState,
    CaptureStatus,
    ChangeAction,
    ChangeSetCheckState,
    ChangeSource,
    FileDisposition,
    MembershipRole,
    ScanState,
)
from crp_core.workflows.contracts import ai_run_workflow_id, change_set_check_workflow_id
from crp_core.workflows.gateway import WorkflowUnavailableError

router = APIRouter(tags=["fix workspaces"], responses={401: {"model": ErrorResponse}})
_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_DONE = (ScanState.SUCCEEDED.value, ScanState.PARTIAL.value)
_ACTIVE_CHECK = (ChangeSetCheckState.QUEUED.value, ChangeSetCheckState.RUNNING.value)
_DONE_CHECK = (ChangeSetCheckState.SUCCEEDED.value, ChangeSetCheckState.PARTIAL.value)
_SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_BLOCKING_FOR_RECIPES = {"suppression_added", "test_weakened", "too_large", "unsafe_path"}
_MAX_FIX_FINDINGS = 2000
_MAX_ISSUES = 10_000
_MAX_CHANGES = 2000
NOT_VERIFIED = "Source-level checks only: not compiled, built or tested by refactorX."


# -- helpers ---------------------------------------------------------------------------------------


def _check(row: ChangeSetCheck, change_set: ChangeSet) -> ChangeSetCheckResponse:
    return ChangeSetCheckResponse(
        id=row.id,
        change_set_id=row.change_set_id,
        state=row.state,
        content_sha256=row.content_sha256,
        current=row.content_sha256 == change_set.content_sha256,
        snapshot_id=row.snapshot_id,
        scan_id=row.scan_id,
        base_scan_id=row.base_scan_id,
        result=row.result,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


async def _latest_check(session: AsyncSession, change_set_id: uuid.UUID) -> ChangeSetCheck | None:
    return await session.scalar(
        select(ChangeSetCheck)
        .where(ChangeSetCheck.change_set_id == change_set_id)
        .order_by(ChangeSetCheck.created_at.desc())
        .limit(1)
    )


async def _base_scan(session: AsyncSession, change_set: ChangeSet) -> uuid.UUID | None:
    if change_set.base_scan_id is not None:
        pinned = await session.get(Scan, change_set.base_scan_id)
        if pinned is not None and pinned.state in _DONE:
            return pinned.id
    return await session.scalar(
        select(Scan.id)
        .where(Scan.snapshot_id == change_set.base_snapshot_id, Scan.state.in_(_DONE))
        .order_by(Scan.created_at.desc())
        .limit(1)
    )


def _state(
    change_set: ChangeSet, latest: ChangeSetCheck | None, events: list[ChangeSetEvent]
) -> str:
    """draft, checking, ready (checked as it is now) or exported (downloaded as it is now)."""
    if latest is not None and latest.state in _ACTIVE_CHECK:
        return "checking"
    newest = events[0] if events else None
    if (
        newest is not None
        and newest.source == ChangeSource.EXPORT.value
        and newest.content_sha256 == change_set.content_sha256
    ):
        return "exported"
    if (
        latest is not None
        and latest.content_sha256 == change_set.content_sha256
        and latest.state in _DONE_CHECK
    ):
        return "ready"
    return "draft"


async def _ai_status(
    session: AsyncSession, container: Any, change_set: ChangeSet
) -> WorkspaceAiStatus:
    policy = await session.get(ProjectAiPolicy, change_set.project_id)
    if policy is None or not policy.enabled:
        return WorkspaceAiStatus(
            available=False,
            reason="AI is switched off for this project. A workspace admin can switch it on in "
            "the project's AI review tab.",
        )
    setup = resolve(container.settings)
    if not setup.available:
        return WorkspaceAiStatus(available=False, reason=setup.reason)
    return WorkspaceAiStatus(available=True, reason=None)


async def _response(
    session: AsyncSession, container: Any, change_set: ChangeSet, can_edit: bool
) -> ChangeSetResponse:
    base = await session.get(Snapshot, change_set.base_snapshot_id)
    source = await session.get(Source, base.source_id) if base else None
    files = await workspace.files_of(session, change_set.id)
    events = list(
        (
            await session.execute(
                select(ChangeSetEvent)
                .where(ChangeSetEvent.change_set_id == change_set.id)
                .order_by(ChangeSetEvent.created_at.desc())
                .limit(40)
            )
        ).scalars()
    )
    latest = await _latest_check(session, change_set.id)
    return ChangeSetResponse(
        state=_state(change_set, latest, events),
        id=change_set.id,
        project_id=change_set.project_id,
        title=change_set.title,
        base_snapshot_id=change_set.base_snapshot_id,
        base_name=source.display_name if source else "upload",
        base_git_commit=base.git_commit if base else None,
        base_scan_id=await _base_scan(session, change_set),
        content_sha256=change_set.content_sha256,
        version=change_set.version,
        can_edit=can_edit,
        ai=await _ai_status(session, container, change_set),
        files=[
            ChangeSetFileSummary(
                path=f.path,
                action=f.action,
                language=f.language,
                size_bytes=f.size_bytes,
                line_count=f.line_count,
                flags=list(f.flags),
                sources=list(f.sources),
                updated_at=f.updated_at,
            )
            for f in files
        ],
        latest_check=_check(latest, change_set) if latest else None,
        events=[
            ChangeSetEventResponse(
                source=e.source,
                path=e.path,
                action=e.action,
                finding_ids=list(e.finding_ids),
                recipe_id=e.recipe_id,
                summary=e.summary,
                flags=list(e.flags),
                created_at=e.created_at,
            )
            for e in events
        ],
        created_at=change_set.created_at,
        updated_at=change_set.updated_at,
    )


async def _editable(
    session: AsyncSession, principal: Principal, change_set_id: uuid.UUID, version: int | None
) -> ChangeSet:
    change_set = await get_scoped(
        session,
        principal,
        ChangeSet,
        change_set_id,
        not_found="workspace_not_found",
        required=MembershipRole.MEMBER,
        for_update=True,
    )
    if change_set.archived_at is not None:
        raise ApiError(409, "workspace_archived", "This workspace is archived")
    if version is not None and version != change_set.version:
        raise ApiError(
            409,
            "version_conflict",
            "The workspace changed since you loaded it; reload and try again",
            {"current_version": change_set.version},
        )
    return change_set


def _can_edit(principal: Principal, change_set: ChangeSet) -> bool:
    return principal.has_role(change_set.workspace_id, MembershipRole.MEMBER)


# -- workspaces ------------------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/change-sets",
    status_code=201,
    response_model=ChangeSetResponse,
    responses=_ERRORS,
)
async def create_change_set(
    project_id: uuid.UUID, body: ChangeSetCreate, principal: CurrentPrincipal, container: Container
) -> ChangeSetResponse:
    """Open a workspace on an upload (default: the latest one)."""
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session,
            principal,
            Project,
            project_id,
            not_found="project_not_found",
            required=MembershipRole.MEMBER,
        )
        if body.snapshot_id is not None:
            snapshot = await session.get(Snapshot, body.snapshot_id)
            if snapshot is None or snapshot.project_id != project.id or snapshot.change_set_id:
                raise ApiError(404, "snapshot_not_found", "Upload not found in this project")
        else:
            snapshot = await session.scalar(
                select(Snapshot)
                .where(
                    Snapshot.project_id == project.id,
                    Snapshot.change_set_id.is_(None),
                    Snapshot.capture_status == CaptureStatus.FROZEN.value,
                )
                .order_by(Snapshot.frozen_at.desc())
                .limit(1)
            )
            if snapshot is None:
                raise ApiError(409, "no_snapshot", "Upload code before opening a workspace")
        if snapshot.capture_status != CaptureStatus.FROZEN.value:
            raise ApiError(409, "snapshot_not_ready", "This upload is not ready yet")
        source = await session.get(Source, snapshot.source_id)
        base_scan = await session.scalar(
            select(Scan.id)
            .where(Scan.snapshot_id == snapshot.id, Scan.state.in_(_DONE))
            .order_by(Scan.created_at.desc())
            .limit(1)
        )
        change_set = ChangeSet(
            workspace_id=project.workspace_id,
            project_id=project.id,
            base_snapshot_id=snapshot.id,
            base_scan_id=base_scan,
            title=body.title or f"Fixes for {source.display_name if source else 'upload'}",
            content_sha256=content_digest([]),
            created_by=principal.user_id,
        )
        session.add(change_set)
        await session.flush()
        await session.refresh(change_set)
        return await _response(session, container, change_set, can_edit=True)


@router.get("/projects/{project_id}/change-sets", response_model=ChangeSetList, responses=_ERRORS)
async def list_change_sets(
    project_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> ChangeSetList:
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        rows = list(
            (
                await session.execute(
                    select(ChangeSet, Source.display_name)
                    .join(Snapshot, Snapshot.id == ChangeSet.base_snapshot_id)
                    .join(Source, Source.id == Snapshot.source_id)
                    .where(ChangeSet.project_id == project.id, ChangeSet.archived_at.is_(None))
                    .order_by(ChangeSet.updated_at.desc())
                    .limit(50)
                )
            ).all()
        )
        items = []
        for change_set, name in rows:
            count = await session.scalar(
                select(func.count()).where(ChangeSetFile.change_set_id == change_set.id)
            )
            latest = await _latest_check(session, change_set.id)
            items.append(
                ChangeSetListItem(
                    id=change_set.id,
                    project_id=change_set.project_id,
                    title=change_set.title,
                    base_snapshot_id=change_set.base_snapshot_id,
                    base_name=name,
                    files_changed=count or 0,
                    latest_check_state=latest.state if latest else None,
                    updated_at=change_set.updated_at,
                )
            )
        return ChangeSetList(items=items)


@router.get("/change-sets/{change_set_id}", response_model=ChangeSetResponse, responses=_ERRORS)
async def get_change_set(
    change_set_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> ChangeSetResponse:
    async with transaction(container.session_factory) as session:
        change_set = await get_scoped(
            session, principal, ChangeSet, change_set_id, not_found="workspace_not_found"
        )
        return await _response(session, container, change_set, _can_edit(principal, change_set))


@router.delete("/change-sets/{change_set_id}", status_code=204, responses=_ERRORS)
async def delete_change_set(
    change_set_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> None:
    """Delete a workspace, its checks and the copies its checks scanned (the upload stays)."""
    async with transaction(container.session_factory) as session:
        change_set = await _editable(session, principal, change_set_id, None)
        active = await session.scalar(
            select(ChangeSetCheck.id).where(
                ChangeSetCheck.change_set_id == change_set.id,
                ChangeSetCheck.state.in_(_ACTIVE_CHECK),
            )
        )
        if active is not None:
            raise ApiError(409, "check_running", "Stop the running check first")
        await session.execute(delete(Snapshot).where(Snapshot.change_set_id == change_set.id))
        await session.delete(change_set)


# -- files -----------------------------------------------------------------------------------------


@router.get(
    "/change-sets/{change_set_id}/file", response_model=WorkspaceFileContent, responses=_ERRORS
)
async def get_file(
    change_set_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    path: Annotated[str, Query(min_length=1, max_length=1024)],
) -> WorkspaceFileContent:
    """The uploaded and current text of one file (for the editor and the comparison)."""
    async with transaction(container.session_factory) as session:
        change_set = await get_scoped(
            session, principal, ChangeSet, change_set_id, not_found="workspace_not_found"
        )
        clean = workspace.canonical(container, path)
        try:
            base_text, current, row, entry = await workspace.current_text(
                session, container, change_set, clean
            )
            undecodable = False
        except ApiError as exc:
            if exc.code != "not_editable":
                raise
            base_text = current = row = None
            entry = await workspace.base_entry(session, change_set.base_snapshot_id, clean)
            undecodable = True
        if entry is None and row is None:
            raise ApiError(404, "file_not_found", "The file is not in the upload or the workspace")
        reason = (
            workspace.NOT_UTF8
            if undecodable
            else workspace.NOT_EDITABLE.get(entry.disposition)
            if entry is not None and entry.disposition != FileDisposition.ANALYZABLE.value
            else None
        )
        crlf = "\r\n" in (base_text or current or "")
        return WorkspaceFileContent(
            path=clean,
            action=row.action if row else None,
            editable=reason is None,
            reason=reason,
            language=(row.language if row else None) or (entry.language if entry else None),
            base_sha256=entry.blob_sha256 if entry else None,
            base_content=base_text,
            sha256=row.sha256 if row else (entry.blob_sha256 if entry else None),
            content=current,
            line_ending="crlf" if crlf else "lf",
            flags=list(row.flags) if row else [],
        )


@router.put(
    "/change-sets/{change_set_id}/file", response_model=WorkspaceSaveResult, responses=_ERRORS
)
async def save_file(
    change_set_id: uuid.UUID,
    body: WorkspaceFileSave,
    principal: CurrentPrincipal,
    container: Container,
) -> WorkspaceSaveResult:
    """Save an edit (or a new file). Policy flags are returned, never silently applied."""
    try:
        async with transaction(container.session_factory) as session:
            change_set = await _editable(session, principal, change_set_id, body.version)
            flags = await workspace.save_text(
                session,
                container,
                change_set,
                path=workspace.canonical(container, body.path),
                text=body.content,
                source=ChangeSource.MANUAL,
                actor=principal.user_id,
                summary="Edited by hand",
                finding_ids=body.finding_ids,
            )
            await session.flush()
            await session.refresh(change_set)
            return WorkspaceSaveResult(
                change_set=await _response(session, container, change_set, True), flags=flags
            )
    except StaleDataError as exc:
        raise ApiError(409, "version_conflict", "The workspace changed meanwhile; reload") from exc


@router.post(
    "/change-sets/{change_set_id}/file/delete", response_model=ChangeSetResponse, responses=_ERRORS
)
async def delete_file(
    change_set_id: uuid.UUID,
    body: WorkspaceFilePath,
    principal: CurrentPrincipal,
    container: Container,
) -> ChangeSetResponse:
    async with transaction(container.session_factory) as session:
        change_set = await _editable(session, principal, change_set_id, body.version)
        await workspace.delete_path(
            session,
            container,
            change_set,
            workspace.canonical(container, body.path),
            principal.user_id,
        )
        await session.flush()
        await session.refresh(change_set)
        return await _response(session, container, change_set, True)


@router.post(
    "/change-sets/{change_set_id}/file/revert", response_model=ChangeSetResponse, responses=_ERRORS
)
async def revert_file(
    change_set_id: uuid.UUID,
    body: WorkspaceFilePath,
    principal: CurrentPrincipal,
    container: Container,
) -> ChangeSetResponse:
    async with transaction(container.session_factory) as session:
        change_set = await _editable(session, principal, change_set_id, body.version)
        await workspace.revert_path(
            session, change_set, workspace.canonical(container, body.path), principal.user_id
        )
        await session.flush()
        await session.refresh(change_set)
        return await _response(session, container, change_set, True)


# -- bulk fixes and the issue queue ----------------------------------------------------------------


@router.post(
    "/change-sets/{change_set_id}/fixes", response_model=WorkspaceFixResult, responses=_ERRORS
)
async def apply_fixes(
    change_set_id: uuid.UUID,
    body: WorkspaceFixRequest,
    principal: CurrentPrincipal,
    container: Container,
) -> WorkspaceFixResult:
    """Apply the deterministic fix of each selected finding (or every finding of one rule).

    Each fix is computed against the upload and applied to the workspace's current text: at its
    exact place, or where the same lines now are; otherwise it is skipped with the reason.
    """
    if not body.finding_ids and not (body.engine and body.rule_id):
        raise ApiError(422, "nothing_selected", "Select findings or a rule")
    applied: list[WorkspaceFixApplied] = []
    skipped: list[WorkspaceFixSkipped] = []
    async with transaction(container.session_factory) as session:
        change_set = await _editable(session, principal, change_set_id, body.version)
        base_scan = await _base_scan(session, change_set)
        if base_scan is None:
            raise ApiError(409, "no_review", "Review the upload before fixing its findings")
        query = (
            select(Finding, FileEntry)
            .join(FileEntry, FileEntry.id == Finding.file_entry_id)
            .where(Finding.scan_id == base_scan)
        )
        if body.finding_ids:
            query = query.where(Finding.id.in_(body.finding_ids))
        else:
            query = query.where(Finding.engine == body.engine, Finding.rule_id == body.rule_id)
        rows = list(
            (
                await session.execute(
                    query.order_by(FileEntry.path, Finding.start_line).limit(_MAX_FIX_FINDINGS)
                )
            ).all()
        )
        found = {f.id for f, _ in rows}
        skipped += [
            WorkspaceFixSkipped(
                finding_id=f, path=None, reason="Not a finding of this upload's review"
            )
            for f in body.finding_ids
            if f not in found
        ]
        readers: dict[str, recipes.ReadFile] = {}
        for finding, entry in rows:
            info = recipes.FindingInfo(
                finding.engine,
                finding.rule_id,
                entry.path,
                finding.start_line,
                finding.end_line,
                finding.details,
            )
            options = recipes.options(info)
            if not options:
                skipped.append(
                    WorkspaceFixSkipped(
                        finding_id=finding.id,
                        path=entry.path,
                        reason="No automatic fix exists for this rule; fix it by hand or with AI",
                    )
                )
                continue
            try:
                base_text, current, _, _ = await workspace.current_text(
                    session, container, change_set, entry.path
                )
            except ApiError as exc:
                if exc.code != "not_editable":
                    raise
                skipped.append(
                    WorkspaceFixSkipped(finding_id=finding.id, path=entry.path, reason=exc.message)
                )
                continue
            if current is None or base_text is None:
                skipped.append(
                    WorkspaceFixSkipped(
                        finding_id=finding.id,
                        path=entry.path,
                        reason="The file is deleted in this workspace",
                    )
                )
                continue
            if options[0].startswith("salesforce:") and entry.path not in readers:
                readers[entry.path] = await sfdx_reader(
                    session, container, change_set.base_snapshot_id, entry.path
                )
            try:
                proposal = recipes.propose(
                    options[0],
                    info,
                    base_text,
                    readers.get(entry.path, lambda _n: None),
                )
                updated = apply_on_current(current, list(proposal.edits))
            except recipes.NoFix as exc:
                skipped.append(
                    WorkspaceFixSkipped(finding_id=finding.id, path=entry.path, reason=str(exc))
                )
                continue
            except PatchError:
                skipped.append(
                    WorkspaceFixSkipped(
                        finding_id=finding.id,
                        path=entry.path,
                        reason="The lines changed in this workspace; fix it by hand",
                    )
                )
                continue
            blocking = (
                set(edit_flags(entry.path, current, updated, strict=True)) & _BLOCKING_FOR_RECIPES
            )
            if blocking:
                skipped.append(
                    WorkspaceFixSkipped(
                        finding_id=finding.id,
                        path=entry.path,
                        reason="The automatic fix was refused by the change policy: "
                        + ", ".join(sorted(blocking)),
                    )
                )
                continue
            await workspace.save_text(
                session,
                container,
                change_set,
                path=entry.path,
                text=updated,
                source=ChangeSource.RECIPE,
                actor=principal.user_id,
                summary=proposal.title,
                finding_ids=[finding.id],
                recipe_id=proposal.recipe_id,
                strict=True,
            )
            applied.append(
                WorkspaceFixApplied(
                    finding_id=finding.id,
                    path=entry.path,
                    recipe_id=proposal.recipe_id,
                    title=proposal.title,
                )
            )
        await session.flush()
        await session.refresh(change_set)
        return WorkspaceFixResult(
            applied=applied,
            skipped=skipped,
            change_set=await _response(session, container, change_set, True),
        )


@router.get(
    "/change-sets/{change_set_id}/issues", response_model=WorkspaceIssuePage, responses=_ERRORS
)
async def list_issues(
    change_set_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    outcome: Annotated[
        Literal["fixed", "still_present", "suppressed", "not_rechecked", "unchecked"] | None,
        Query(),
    ] = None,
    severity: Annotated[str | None, Query(max_length=16)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    fixable: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
) -> WorkspaceIssuePage:
    """The upload's findings as a work queue, with their outcome from the latest check."""
    async with transaction(container.session_factory) as session:
        change_set = await get_scoped(
            session, principal, ChangeSet, change_set_id, not_found="workspace_not_found"
        )
        base_scan = await _base_scan(session, change_set)
        if base_scan is None:
            return WorkspaceIssuePage(items=[], total=0, next_cursor=None)
        query = (
            select(Finding, FileEntry.path)
            .join(FileEntry, FileEntry.id == Finding.file_entry_id)
            .where(Finding.scan_id == base_scan)
        )
        if severity:
            query = query.where(Finding.severity == severity)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(
                or_(
                    Finding.title.ilike(f"%{escaped}%", escape="\\"),
                    FileEntry.path.ilike(f"%{escaped}%", escape="\\"),
                )
            )
        rows = list((await session.execute(query.limit(_MAX_ISSUES))).all())
        latest = await _latest_check(session, change_set.id)
        outcomes: dict[str, str] = {}
        stored = latest.result.get("outcomes") if latest is not None and latest.result else None
        if isinstance(stored, dict):
            outcomes = {str(k): str(v) for k, v in stored.items()}
        changed = {f.path for f in await workspace.files_of(session, change_set.id)}
        items: list[WorkspaceIssue] = []
        for finding, path in rows:
            info = recipes.FindingInfo(
                finding.engine,
                finding.rule_id,
                path,
                finding.start_line,
                finding.end_line,
                finding.details,
            )
            item = WorkspaceIssue(
                finding_id=finding.id,
                title=finding.title,
                severity=finding.severity,
                category=finding.category,
                engine=finding.engine,
                rule_id=finding.rule_id,
                path=path,
                line=finding.start_line,
                recipe_available=bool(recipes.options(info)),
                changed=path in changed,
                outcome=outcomes.get(str(finding.id)),
            )
            if outcome == "unchecked" and item.outcome is not None:
                continue
            if outcome not in {None, "unchecked"} and item.outcome != outcome:
                continue
            if fixable is not None and item.recipe_available != fixable:
                continue
            items.append(item)
        items.sort(key=lambda i: (_SEVERITY.get(i.severity, 9), i.path, i.line or 0))
        offset = decode_offset(cursor)
        page = items[offset : offset + limit]
        following = offset + limit
        return WorkspaceIssuePage(
            items=page,
            total=len(items),
            next_cursor=encode_offset(following) if following < len(items) else None,
        )


# -- AI fix suggestions ----------------------------------------------------------------------------

_AI_ACTIVE = (AiRunState.QUEUED.value, AiRunState.RUNNING.value)
_AI_DONE = (AiRunState.SUCCEEDED.value, AiRunState.PARTIAL.value)


@router.post(
    "/change-sets/{change_set_id}/ai-fixes",
    status_code=202,
    response_model=AiRunResponse,
    responses={**_ERRORS, 429: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def request_ai_fix(
    change_set_id: uuid.UUID,
    body: WorkspaceAiFixRequest,
    principal: CurrentPrincipal,
    container: Container,
) -> AiRunResponse:
    """Ask AI for candidate fixes of one finding, made for the file as it is in the workspace.

    Only when the project allows AI and the server has a provider with budget left. Candidates
    are checked like automatic fixes before anyone can apply them; nothing changes until then.
    """
    setup = resolve(container.settings)
    async with transaction(container.session_factory) as session:
        change_set = await _editable(session, principal, change_set_id, None)
        policy = await ai_gate(session, setup, change_set.project_id)
        base_scan = await _base_scan(session, change_set)
        row = (
            await session.execute(
                select(Finding, FileEntry)
                .join(FileEntry, FileEntry.id == Finding.file_entry_id)
                .where(Finding.id == body.finding_id, Finding.scan_id == base_scan)
            )
        ).first()
        if row is None or base_scan is None:
            raise ApiError(404, "finding_not_found", "Not a finding of this upload's review")
        finding, entry = row
        if finding.start_line is None:
            raise ApiError(422, "no_location", "AI fixes need a finding with a line in a file")
        if entry.disposition != FileDisposition.ANALYZABLE.value:
            raise ApiError(409, "not_editable", workspace.NOT_EDITABLE.get(entry.disposition, ""))
        _, current, _, _ = await workspace.current_text(session, container, change_set, entry.path)
        if current is None:
            raise ApiError(409, "file_deleted", "The file is deleted in this workspace")
        running = await session.scalar(
            select(AiRun.id).where(
                AiRun.change_set_id == change_set.id,
                AiRun.finding_id == finding.id,
                AiRun.state.in_(_AI_ACTIVE),
            )
        )
        if running is not None:
            raise ApiError(409, "ai_fix_running", "AI is already preparing fixes for this issue")
        run = AiRun(
            workspace_id=change_set.workspace_id,
            project_id=change_set.project_id,
            snapshot_id=change_set.base_snapshot_id,
            scan_id=base_scan,
            finding_id=finding.id,
            kind=AiRunKind.FIX.value,
            state=AiRunState.QUEUED.value,
            target_paths=[entry.path],
            change_set_id=change_set.id,
            target_sha256=hashlib.sha256(current.encode("utf-8")).hexdigest(),
            provider=setup.provider.value,
            model=setup.model or "",
            prompt_version=FIX_PROMPT_VERSION,
            requested_by=principal.user_id,
            limits=run_limits(setup, policy),
        )
        session.add(run)
        await session.flush()
        run.workflow_id = ai_run_workflow_id(run.id)
        await session.refresh(run)
        run_id = run.id
        response = run_response(run, [])
    try:
        await container.workflows.start_ai_run(run_id)
    except WorkflowUnavailableError as exc:
        async with transaction(container.session_factory) as session:
            stored = await session.get(AiRun, run_id, with_for_update=True)
            if stored is not None and stored.state == AiRunState.QUEUED.value:
                stored.state = AiRunState.FAILED.value
                stored.error_code = "workflow_unavailable"
                stored.error_message = "The AI service is not running; nothing was sent."
        raise ApiError(503, "workflow_unavailable", f"{exc}; try again shortly") from exc
    return response


@router.get("/change-sets/{change_set_id}/ai-fixes", response_model=AiRunPage, responses=_ERRORS)
async def list_ai_fixes(
    change_set_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    path: Annotated[str | None, Query(max_length=1024)] = None,
    finding_id: uuid.UUID | None = None,
) -> AiRunPage:
    """AI fix suggestions requested in this workspace, newest first (optionally one file)."""
    async with transaction(container.session_factory) as session:
        change_set = await get_scoped(
            session, principal, ChangeSet, change_set_id, not_found="workspace_not_found"
        )
        query = select(AiRun).where(AiRun.change_set_id == change_set.id)
        if finding_id is not None:
            query = query.where(AiRun.finding_id == finding_id)
        runs = list(
            (await session.execute(query.order_by(AiRun.created_at.desc()).limit(50))).scalars()
        )
    items = [run_response(run, []) for run in runs if path is None or run.target_paths == [path]]
    return AiRunPage(items=items[:20])


@router.post(
    "/change-sets/{change_set_id}/ai-fixes/{run_id}/apply",
    response_model=WorkspaceSaveResult,
    responses=_ERRORS,
)
async def apply_ai_fix(
    change_set_id: uuid.UUID,
    run_id: uuid.UUID,
    body: WorkspaceAiFixApply,
    principal: CurrentPrincipal,
    container: Container,
) -> WorkspaceSaveResult:
    """Apply one checked AI candidate to the workspace (recorded as an AI change)."""
    try:
        async with transaction(container.session_factory) as session:
            change_set = await _editable(session, principal, change_set_id, body.version)
            run = await session.get(AiRun, run_id, with_for_update=True)
            if run is None or run.change_set_id != change_set.id or run.kind != AiRunKind.FIX:
                raise ApiError(404, "ai_fix_not_found", "AI suggestion not found in this workspace")
            if run.state not in _AI_DONE or not run.answer or not run.target_paths:
                raise ApiError(409, "ai_fix_not_ready", "These suggestions are not ready")
            stored = run.answer.get("candidates")
            candidates: list[dict[str, Any]] = (
                [c for c in stored if isinstance(c, dict)] if isinstance(stored, list) else []
            )
            chosen = next((c for c in candidates if c.get("index") == body.candidate), None)
            if chosen is None:
                raise ApiError(404, "candidate_not_found", "This suggestion does not exist")
            if not chosen.get("applicable"):
                raise ApiError(
                    409,
                    "candidate_not_applicable",
                    str(chosen.get("reason") or "This suggestion did not pass the checks"),
                )
            if chosen.get("applied_at"):
                raise ApiError(409, "candidate_applied", "This suggestion is already applied")
            path = run.target_paths[0]
            _, current, _, _ = await workspace.current_text(session, container, change_set, path)
            if current is None:
                raise ApiError(409, "file_deleted", "The file is deleted in this workspace")
            edits = [Edit.from_json(e) for e in chosen.get("edits") or []]
            exact = hashlib.sha256(current.encode("utf-8")).hexdigest() == run.target_sha256
            try:
                updated = apply_edits(current, edits) if exact else apply_on_current(current, edits)
            except PatchError as exc:
                raise ApiError(
                    409,
                    "ai_fix_stale",
                    "The file changed since the suggestion was made; ask AI again",
                ) from exc
            blocking = set(edit_flags(path, current, updated, strict=True)) & _BLOCKING_FOR_RECIPES
            if blocking:
                raise ApiError(
                    422,
                    "fix_not_allowed",
                    "The change policy refuses this change: " + ", ".join(sorted(blocking)),
                )
            flags = await workspace.save_text(
                session,
                container,
                change_set,
                path=path,
                text=updated,
                source=ChangeSource.AI,
                actor=principal.user_id,
                summary=f"AI suggestion: {chosen.get('title') or 'fix'}",
                finding_ids=[run.finding_id] if run.finding_id else [],
                strict=True,
            )
            run.answer = {
                **run.answer,
                "candidates": [
                    {**c, "applied_at": datetime.now(UTC).isoformat()}
                    if c.get("index") == body.candidate
                    else c
                    for c in candidates
                ],
            }
            await session.flush()
            await session.refresh(change_set)
            return WorkspaceSaveResult(
                change_set=await _response(session, container, change_set, True), flags=flags
            )
    except StaleDataError as exc:
        raise ApiError(409, "version_conflict", "The workspace changed meanwhile; reload") from exc


# -- checks ----------------------------------------------------------------------------------------


@router.post(
    "/change-sets/{change_set_id}/checks",
    status_code=202,
    response_model=ChangeSetCheckResponse,
    responses={**_ERRORS, 503: {"model": ErrorResponse}},
)
async def start_check(
    change_set_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> ChangeSetCheckResponse:
    """Re-check the workspace: its edits on a copy of the upload, scanned and compared."""
    async with transaction(container.session_factory) as session:
        change_set = await _editable(session, principal, change_set_id, None)
        files = await workspace.files_of(session, change_set.id)
        if not files:
            raise ApiError(422, "nothing_to_check", "The workspace has no changes yet")
        running = await session.scalar(
            select(ChangeSetCheck.id).where(
                ChangeSetCheck.change_set_id == change_set.id,
                ChangeSetCheck.state.in_(_ACTIVE_CHECK),
            )
        )
        if running is not None:
            raise ApiError(409, "check_running", "A check of this workspace is running")
        check = ChangeSetCheck(
            change_set_id=change_set.id,
            workspace_id=change_set.workspace_id,
            state=ChangeSetCheckState.QUEUED.value,
            content_sha256=change_set.content_sha256,
            files=[
                {
                    "path": f.path,
                    "action": f.action,
                    "base_sha256": f.base_sha256,
                    "sha256": f.sha256,
                    "size_bytes": f.size_bytes,
                    "line_count": f.line_count,
                    "flags": list(f.flags),
                }
                for f in files
            ],
            requested_by=principal.user_id,
        )
        session.add(check)
        await session.flush()
        check.workflow_id = change_set_check_workflow_id(check.id)
        await session.refresh(check)
        response = _check(check, change_set)
    try:
        await container.workflows.start_change_set_check(check.id)
    except WorkflowUnavailableError as exc:
        async with transaction(container.session_factory) as session:
            stored = await session.get(ChangeSetCheck, check.id, with_for_update=True)
            if stored is not None and stored.state == ChangeSetCheckState.QUEUED.value:
                stored.state = ChangeSetCheckState.FAILED.value
                stored.error_code = "workflow_unavailable"
                stored.error_message = "The check service is not running; try again shortly."
                stored.finished_at = datetime.now(UTC)
        raise ApiError(503, "workflow_unavailable", f"{exc}; try again shortly") from exc
    return response


@router.get(
    "/change-set-checks/{check_id}", response_model=ChangeSetCheckResponse, responses=_ERRORS
)
async def get_check(
    check_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> ChangeSetCheckResponse:
    async with transaction(container.session_factory) as session:
        check = await get_scoped(
            session, principal, ChangeSetCheck, check_id, not_found="check_not_found"
        )
        change_set = await session.get(ChangeSet, check.change_set_id)
        if change_set is None:
            raise ApiError(404, "check_not_found", "Check not found")
        return _check(check, change_set)


@router.post(
    "/change-set-checks/{check_id}/cancel", response_model=ChangeSetCheckResponse, responses=_ERRORS
)
async def cancel_check(
    check_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> ChangeSetCheckResponse:
    async with transaction(container.session_factory) as session:
        check = await get_scoped(
            session,
            principal,
            ChangeSetCheck,
            check_id,
            not_found="check_not_found",
            required=MembershipRole.MEMBER,
            for_update=True,
        )
        if not ChangeSetCheckState(check.state).is_terminal:
            check.cancel_requested_at = check.cancel_requested_at or datetime.now(UTC)
        change_set = await session.get(ChangeSet, check.change_set_id)
        if change_set is None:
            raise ApiError(404, "check_not_found", "Check not found")
        response = _check(check, change_set)
    try:
        await container.workflows.cancel_change_set_check(check_id)
    except WorkflowUnavailableError as exc:
        raise ApiError(503, "workflow_unavailable", str(exc)) from exc
    return response


# -- exports ---------------------------------------------------------------------------------------


async def _export_material(
    session: AsyncSession, container: Any, change_set: ChangeSet
) -> dict[str, Any]:
    base = await session.get(Snapshot, change_set.base_snapshot_id)
    if base is None:
        raise ApiError(404, "snapshot_not_found", "The upload was deleted")
    source = await session.get(Source, base.source_id)
    files = await workspace.files_of(session, change_set.id)
    if not files:
        raise ApiError(422, "nothing_to_export", "The workspace has no changes yet")
    listed = (base.git_capture or {}).get("executable")
    executable = {str(path) for path in listed} if isinstance(listed, list) else set()
    entries: list[dict[str, Any]] = []
    for row in files:
        base_text = after = None
        if row.base_sha256:
            base_text = await read_blob_text(container, row.base_sha256)
        if row.sha256:
            after = await read_blob_text(container, row.sha256)
        entries.append(
            {
                "row": row,
                "before": base_text,
                "after": after,
                "mode": "100755" if row.path in executable else "100644",
            }
        )
    latest = await _latest_check(session, change_set.id)
    return {
        "base": base,
        "source": source,
        "entries": entries,
        "executable": executable,
        "check": latest if latest and latest.content_sha256 == change_set.content_sha256 else None,
    }


def _patch_text(change_set: ChangeSet, material: dict[str, Any]) -> str:
    base: Snapshot = material["base"]
    header = [
        f"# refactorX workspace {change_set.id}: {change_set.title}",
        f"# Upload (snapshot) {base.id}, manifest sha256 {base.manifest_sha256}",
        f"# Workspace content sha256 {change_set.content_sha256}; {len(material['entries'])} files",
        "# Apply with `git apply -p1` in a copy of exactly this upload. " + NOT_VERIFIED,
        "",
    ]
    body = "".join(
        file_patch(item["row"].path, item["before"], item["after"], mode=item["mode"])
        for item in material["entries"]
    )
    return "\n".join(header) + body


def _summary(change_set: ChangeSet, material: dict[str, Any], project_name: str) -> dict[str, Any]:
    base: Snapshot = material["base"]
    source: Source | None = material["source"]
    check: ChangeSetCheck | None = material["check"]
    result = (check.result or {}) if check else {}
    new_items = result.get("new_items")
    return {
        "format": EXPORT_FORMAT,
        "generated_at": datetime.now(UTC).isoformat(),
        "tool": {"name": "refactorX", "component": "workspace", "version": __version__},
        "project": {"id": str(change_set.project_id), "name": project_name},
        "workspace": {
            "id": str(change_set.id),
            "title": change_set.title,
            "content_sha256": change_set.content_sha256,
        },
        "base": {
            "snapshot_id": str(base.id),
            "manifest_sha256": base.manifest_sha256,
            "name": source.display_name if source else None,
            "git_commit": base.git_commit,
            "git_ref": base.git_ref,
        },
        "files": [
            {
                "path": item["row"].path,
                "action": item["row"].action,
                "base_sha256": item["row"].base_sha256,
                "sha256": item["row"].sha256,
                "sources": list(item["row"].sources),
                "flags": list(item["row"].flags),
            }
            for item in material["entries"]
        ],
        "deleted": [
            item["row"].path
            for item in material["entries"]
            if item["row"].action == ChangeAction.DELETE.value
        ],
        "check": {
            "checked": check is not None,
            "state": check.state if check else None,
            "counts": result.get("counts") if check else None,
            "new_items": new_items[:50] if isinstance(new_items, list) else [],
        },
        "not_verified": NOT_VERIFIED,
        "how_to_apply": [
            "Patch: in a copy of exactly this upload run `git apply -p1 <file>.diff`.",
            "Changed files: copy them over the same paths; delete the files listed as deleted.",
        ],
    }


def _summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# {summary['workspace']['title']}",
        "",
        f"Upload: {summary['base']['name'] or summary['base']['snapshot_id']}"
        + (
            f" (commit {summary['base']['git_commit'][:7]})"
            if summary["base"]["git_commit"]
            else ""
        ),
        f"Workspace content: `{summary['workspace']['content_sha256'][:12]}`",
        "",
        "## Files",
        "",
    ]
    for item in summary["files"]:
        flags = f" — flags: {', '.join(item['flags'])}" if item["flags"] else ""
        lines.append(f"- {item['action']}: `{item['path']}` ({', '.join(item['sources'])}){flags}")
    lines += ["", "## Check", ""]
    check = summary["check"]
    if check["checked"]:
        counts = check["counts"] or {}
        lines.append(
            f"{check['state']}: {counts.get('fixed', 0)} fixed, {counts.get('still_present', 0)} "
            f"still present, {counts.get('suppressed', 0)} suppressed (not fixed), "
            f"{counts.get('not_rechecked', 0)} not rechecked, {counts.get('new', 0)} new."
        )
    else:
        lines.append("Not checked since the last change.")
    lines += ["", summary["not_verified"], ""]
    return "\n".join(lines)


def _mbox_text(change_set: ChangeSet, material: dict[str, Any]) -> str:
    """One commit for ``git am`` (author refactorX; amend it to take ownership)."""
    base: Snapshot = material["base"]
    subject = " ".join(change_set.title.split())[:200] or "refactorX fixes"
    if not subject.isascii():
        subject = Header(subject, "utf-8").encode()
    body = "".join(
        file_patch(item["row"].path, item["before"], item["after"], mode=item["mode"])
        for item in material["entries"]
    )
    message = [
        f"From {change_set.content_sha256[:40]} Mon Sep 17 00:00:00 2001",
        "From: refactorX <noreply@refactorx.invalid>",
        f"Date: {format_datetime(datetime.now(UTC))}",
        f"Subject: [PATCH] {subject}",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=UTF-8",
        "Content-Transfer-Encoding: 8bit",
        "",
        f"Fixes prepared in refactorX workspace {change_set.id}.",
        "",
        f"Upload (snapshot) {base.id}, manifest sha256 {base.manifest_sha256}.",
        f"Workspace content sha256 {change_set.content_sha256}.",
        NOT_VERIFIED,
        "---",
        "",
    ]
    return "\n".join(message) + body + f"-- \nrefactorX {__version__}\n\n"


ZipPut = Callable[[str, bytes, str], None]


async def _zip_file(
    container: Any, build: Callable[[ZipPut], None], name: str, stamp: datetime
) -> FileResponse:
    """Build a ZIP off the event loop into a temporary file that is removed after sending.

    Entries keep the file mode (executable or not) and one timestamp, so the same workspace
    content always produces the same archive entries.
    """
    root = Path(container.settings.work_root) / "exports"
    when = stamp.astimezone(UTC).timetuple()[:6]

    def run() -> str:
        root.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(prefix="crp-export-", suffix=".zip", dir=root)
        try:
            with (
                os.fdopen(handle, "wb") as sink,
                zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED) as archive,
            ):

                def put(path: str, data: bytes, mode: str = "100644") -> None:
                    info = zipfile.ZipInfo(path, date_time=when)
                    info.external_attr = int(mode, 8) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, data)

                build(put)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
        return temp

    temp = await asyncio.to_thread(run)
    return FileResponse(
        temp,
        media_type="application/zip",
        filename=name,
        background=BackgroundTask(os.unlink, temp),
        headers={"Cache-Control": "no-store"},
    )


def _attachment(name: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"}


@router.get(
    "/change-sets/{change_set_id}/export",
    responses={**_ERRORS, 200: {"content": {"application/zip": {}, "text/x-diff": {}}}},
)
async def export_change_set(
    change_set_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    format: Annotated[
        Literal["patch", "mbox", "changed", "full", "summary", "summary-md"], Query()
    ] = "patch",
) -> Response:
    """Download the workspace: a patch (``git apply``) or one commit (``git am``), only the
    changed files, the full project, or a summary. Every download names its exact upload."""
    async with transaction(container.session_factory) as session:
        change_set = await get_scoped(
            session, principal, ChangeSet, change_set_id, not_found="workspace_not_found"
        )
        project = await session.get(Project, change_set.project_id)
        material = await _export_material(session, container, change_set)
        summary = _summary(change_set, material, project.name if project else "")
        base_rows: list[tuple[str, str, str | None, str | None]] = []
        if format == "full":
            base_rows = [
                (path, disposition, reason, sha)
                for path, disposition, reason, sha in (
                    await session.execute(
                        select(
                            FileEntry.path,
                            FileEntry.disposition,
                            FileEntry.reason,
                            FileEntry.blob_sha256,
                        ).where(FileEntry.snapshot_id == change_set.base_snapshot_id)
                    )
                ).all()
            ]
        session.add(workspace.export_event(change_set, principal.user_id, format))
    stem = f"refactorx-workspace-{change_set.content_sha256[:8]}"
    if format == "patch":
        return PlainTextResponse(
            _patch_text(change_set, material),
            media_type="text/x-diff",
            headers=_attachment(f"{stem}.diff"),
        )
    if format == "mbox":
        return PlainTextResponse(
            _mbox_text(change_set, material),
            media_type="application/mbox",
            headers=_attachment(f"{stem}.patch"),
        )
    if format == "summary":
        return JSONResponse(summary, headers=_attachment(f"{stem}.json"))
    if format == "summary-md":
        return PlainTextResponse(
            _summary_markdown(summary),
            media_type="text/markdown",
            headers=_attachment(f"{stem}.md"),
        )
    patch = _patch_text(change_set, material).encode("utf-8")
    manifest = json.dumps(summary, indent=2).encode("utf-8")
    entries: list[dict[str, Any]] = material["entries"]
    stamp = change_set.updated_at
    if format == "changed":

        def build_changed(put: ZipPut) -> None:
            for item in entries:
                if item["after"] is not None:
                    put(item["row"].path, item["after"].encode("utf-8"), item["mode"])
            put("refactorx-changes.json", manifest, "100644")
            put("refactorx-changes.diff", patch, "100644")

        return await _zip_file(container, build_changed, f"{stem}-changed-files.zip", stamp)

    changed = {item["row"].path: item for item in entries}
    executable: set[str] = material["executable"]
    store = container.artifacts
    limit = container.settings.intake_max_text_file_bytes

    def build_full(put: ZipPut) -> None:
        not_included: list[str] = []
        for path, disposition, reason, sha in sorted(base_rows):
            if path in changed:
                continue
            if disposition == FileDisposition.ANALYZABLE.value and sha:
                # Unchanged files are copied byte for byte (whatever their encoding).
                data = store.read_bytes(ArtifactKey(blob_key(sha)), max_bytes=limit)
                put(path, data, "100755" if path in executable else "100644")
            else:
                not_included.append(f"{path} ({disposition.lower()}: {reason or 'not stored'})")
        for path, item in sorted(changed.items()):
            if item["after"] is not None:
                put(path, item["after"].encode("utf-8"), item["mode"])
        put("refactorx-changes.json", manifest, "100644")
        note = "Files refactorX did not store (copy them from your original project):\n"
        put(
            "refactorx-not-included.txt", (note + "\n".join(not_included) + "\n").encode(), "100644"
        )

    return await _zip_file(container, build_full, f"{stem}-full-project.zip", stamp)


# -- comparing uploads -----------------------------------------------------------------------------


async def _analyzable(session: AsyncSession, snapshot_id: uuid.UUID) -> dict[str, str]:
    rows = await session.execute(
        select(FileEntry.path, FileEntry.blob_sha256).where(
            FileEntry.snapshot_id == snapshot_id,
            FileEntry.disposition == FileDisposition.ANALYZABLE.value,
        )
    )
    return {path: sha for path, sha in rows.all() if sha}


async def _pair(
    session: AsyncSession, principal: Principal, snapshot_id: uuid.UUID, base: uuid.UUID
) -> tuple[Snapshot, Snapshot]:
    head = await get_scoped(
        session, principal, Snapshot, snapshot_id, not_found="snapshot_not_found"
    )
    older = await get_scoped(session, principal, Snapshot, base, not_found="snapshot_not_found")
    if older.project_id != head.project_id:
        raise ApiError(422, "different_projects", "Both uploads must belong to one project")
    return head, older


@router.get(
    "/snapshots/{snapshot_id}/compare",
    response_model=SnapshotComparisonResponse,
    responses=_ERRORS,
)
async def compare_snapshots(
    snapshot_id: uuid.UUID,
    base: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
) -> SnapshotComparisonResponse:
    """Files added, changed, removed and renamed between two uploads (or commits)."""
    async with transaction(container.session_factory) as session:
        head, older = await _pair(session, principal, snapshot_id, base)
        changes = diff_manifests(
            await _analyzable(session, older.id), await _analyzable(session, head.id)
        )
    rows = (
        [SnapshotChange(path=p, status="added", previous_path=None) for p in changes.added]
        + [SnapshotChange(path=p, status="modified", previous_path=None) for p in changes.modified]
        + [SnapshotChange(path=p, status="removed", previous_path=None) for p in changes.removed]
        + [
            SnapshotChange(path=new, status="renamed", previous_path=old)
            for old, new in changes.renamed
        ]
    )
    rows.sort(key=lambda r: r.path)
    return SnapshotComparisonResponse(
        base_snapshot_id=older.id,
        snapshot_id=head.id,
        counts={
            "added": len(changes.added),
            "modified": len(changes.modified),
            "removed": len(changes.removed),
            "renamed": len(changes.renamed),
        },
        changes=rows[:_MAX_CHANGES],
        truncated=len(rows) > _MAX_CHANGES,
    )


@router.get(
    "/snapshots/{snapshot_id}/compare/file", response_model=FileComparison, responses=_ERRORS
)
async def compare_file(
    snapshot_id: uuid.UUID,
    base: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    path: Annotated[str, Query(min_length=1, max_length=1024)],
    previous_path: Annotated[str | None, Query(max_length=1024)] = None,
) -> FileComparison:
    """Both versions of one file, as text, for the side-by-side view."""
    async with transaction(container.session_factory) as session:
        head, older = await _pair(session, principal, snapshot_id, base)
        before_entry = await workspace.base_entry(session, older.id, previous_path or path)
        after_entry = await workspace.base_entry(session, head.id, path)

        async def text(entry: FileEntry | None, missing: str) -> tuple[str | None, str | None]:
            if entry is None:
                return None, missing
            if entry.disposition != FileDisposition.ANALYZABLE.value or not entry.blob_sha256:
                return None, workspace.NOT_EDITABLE.get(entry.disposition, "Not stored.")
            return await read_blob_text(container, entry.blob_sha256), None

        before, before_note = await text(before_entry, "New in this upload.")
        after, after_note = await text(after_entry, "Removed in this upload.")
    return FileComparison(
        path=path,
        previous_path=previous_path,
        before=before,
        after=after,
        before_sha256=before_entry.blob_sha256 if before_entry else None,
        after_sha256=after_entry.blob_sha256 if after_entry else None,
        note=" ".join(dict.fromkeys(n for n in (before_note, after_note) if n)) or None,
    )
