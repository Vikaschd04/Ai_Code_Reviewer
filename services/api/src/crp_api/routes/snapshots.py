"""Frozen snapshots, their manifests (file entries), bounded source content and symbols."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from crp_analysis.manifest import blob_key
from crp_analysis.redaction import redact_lines
from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import (
    FileContentResponse,
    FileEntryResponse,
    FilePage,
    SnapshotPage,
    SnapshotResponse,
    SymbolList,
    SymbolResponse,
)
from crp_api.services.scope import decode_offset, encode_offset, get_scoped
from crp_core.artifacts import ArtifactKey, ArtifactStore
from crp_core.db.models import CodeSymbol, FileEntry, Project, Snapshot, Source
from crp_core.db.session import transaction

router = APIRouter(tags=["snapshots"], responses={401: {"model": ErrorResponse}})
_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse}}
MAX_CONTENT_LINES = 2000
_DISPOSITIONS = {"ANALYZABLE", "BINARY", "OVERSIZED", "EXCLUDED"}


def snapshot_response(snapshot: Snapshot, source: Source) -> SnapshotResponse:
    return SnapshotResponse(
        id=snapshot.id,
        project_id=snapshot.project_id,
        source_id=snapshot.source_id,
        source_mode=source.mode,
        source_name=source.display_name,
        intake_id=snapshot.intake_id,
        capture_status=snapshot.capture_status,
        manifest_sha256=snapshot.manifest_sha256,
        manifest_version=snapshot.manifest_version,
        policy_version=snapshot.policy_version,
        file_count=snapshot.file_count,
        analyzable_count=snapshot.analyzable_count,
        excluded_count=snapshot.excluded_count,
        total_bytes=snapshot.total_bytes,
        git_commit=snapshot.git_commit,
        git_ref=snapshot.git_ref,
        git_provider=snapshot.git_provider,
        git_repository=snapshot.git_repository,
        git_tree_sha=snapshot.git_tree_sha,
        git_capture=snapshot.git_capture,
        frozen_at=snapshot.frozen_at,
        created_at=snapshot.created_at,
        inventory=snapshot.inventory,
    )


@router.get("/projects/{project_id}/snapshots", response_model=SnapshotPage, responses=_NOT_FOUND)
async def list_snapshots(
    project_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> SnapshotPage:
    async with transaction(container.session_factory) as session:
        project = await get_scoped(
            session, principal, Project, project_id, not_found="project_not_found"
        )
        rows = (
            await session.execute(
                select(Snapshot, Source)
                .join(Source, Source.id == Snapshot.source_id)
                .where(Snapshot.project_id == project.id)
                .order_by(Snapshot.created_at.desc())
                .limit(50)
            )
        ).all()
        return SnapshotPage(items=[snapshot_response(s, src) for s, src in rows])


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotResponse, responses=_NOT_FOUND)
async def get_snapshot(
    snapshot_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> SnapshotResponse:
    async with transaction(container.session_factory) as session:
        snapshot = await get_scoped(
            session, principal, Snapshot, snapshot_id, not_found="snapshot_not_found"
        )
        source = await session.get(Source, snapshot.source_id)
        if source is None:
            raise ApiError(404, "snapshot_not_found", "Snapshot not found")
        return snapshot_response(snapshot, source)


def _file_response(row: FileEntry) -> FileEntryResponse:
    return FileEntryResponse(
        id=row.id,
        path=row.path,
        disposition=row.disposition,
        reason=row.reason,
        size_bytes=row.size_bytes,
        sha256=row.blob_sha256,
        language=row.language,
        category=row.category,
        line_count=row.line_count,
        parse_status=row.parse_status,
        parse_error_count=row.parse_error_count,
    )


@router.get("/snapshots/{snapshot_id}/files", response_model=FilePage, responses=_NOT_FOUND)
async def list_files(
    snapshot_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    disposition: Annotated[str | None, Query(max_length=16)] = None,
    language: Annotated[str | None, Query(max_length=32)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
) -> FilePage:
    if disposition is not None and disposition not in _DISPOSITIONS:
        raise ApiError(400, "invalid_filter", "Unknown disposition filter")
    offset = decode_offset(cursor)
    async with transaction(container.session_factory) as session:
        snapshot = await get_scoped(
            session, principal, Snapshot, snapshot_id, not_found="snapshot_not_found"
        )
        query = select(FileEntry).where(FileEntry.snapshot_id == snapshot.id)
        if disposition:
            query = query.where(FileEntry.disposition == disposition)
        if language:
            query = query.where(FileEntry.language == language)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(FileEntry.path.ilike(f"%{escaped}%", escape="\\"))
        total = (
            await session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rows = list(
            (
                await session.execute(
                    query.order_by(FileEntry.path).offset(offset).limit(limit + 1)
                )
            ).scalars()
        )
    return FilePage(
        items=[_file_response(r) for r in rows[:limit]],
        next_cursor=encode_offset(offset + limit) if len(rows) > limit else None,
        total=total,
    )


def read_lines(store: ArtifactStore, entry: FileEntry, max_bytes: int) -> list[str]:
    if entry.blob_sha256 is None:
        return []
    data = store.read_bytes(ArtifactKey(blob_key(entry.blob_sha256)), max_bytes=max_bytes)
    return data.decode("utf-8", errors="replace").splitlines()


async def file_content(
    container: Any, entry: FileEntry, start_line: int, end_line: int | None
) -> FileContentResponse:
    if entry.disposition != "ANALYZABLE":
        raise ApiError(
            409,
            "content_not_stored",
            f"Content is not stored for {entry.disposition.lower()} files",
        )
    lines = await asyncio.to_thread(
        read_lines, container.artifacts, entry, container.settings.intake_max_text_file_bytes
    )
    total = len(lines)
    start = max(1, start_line)
    requested_end = end_line if end_line is not None else start + MAX_CONTENT_LINES - 1
    end = min(total, requested_end, start + MAX_CONTENT_LINES - 1)
    window, redactions = redact_lines(lines[start - 1 : end]) if start <= total else ([], 0)
    return FileContentResponse(
        file_id=entry.id,
        path=entry.path,
        language=entry.language,
        total_lines=total,
        start_line=start,
        end_line=max(end, start - 1),
        lines=window,
        redactions=redactions,
        truncated=end < min(total, requested_end),
    )


@router.get(
    "/files/{file_id}/content",
    response_model=FileContentResponse,
    responses={**_NOT_FOUND, 409: {"model": ErrorResponse}},
)
async def get_file_content(
    file_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    start_line: Annotated[int, Query(ge=1)] = 1,
    end_line: Annotated[int | None, Query(ge=1)] = None,
) -> FileContentResponse:
    """Bounded, authorized source lines as JSON strings (never HTML); likely secrets are masked."""
    async with transaction(container.session_factory) as session:
        entry = await get_scoped(session, principal, FileEntry, file_id, not_found="file_not_found")
    return await file_content(container, entry, start_line, end_line)


@router.get("/files/{file_id}/symbols", response_model=SymbolList, responses=_NOT_FOUND)
async def get_file_symbols(
    file_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> SymbolList:
    async with transaction(container.session_factory) as session:
        entry = await get_scoped(session, principal, FileEntry, file_id, not_found="file_not_found")
        rows = list(
            (
                await session.execute(
                    select(CodeSymbol)
                    .where(CodeSymbol.file_entry_id == entry.id)
                    .order_by(CodeSymbol.start_line, CodeSymbol.start_column)
                    .limit(501)
                )
            ).scalars()
        )
    return SymbolList(
        items=[
            SymbolResponse(
                kind=r.kind,
                name=r.name,
                container=r.container,
                start_line=r.start_line,
                end_line=r.end_line,
            )
            for r in rows[:500]
        ],
        truncated=len(rows) > 500,
    )
