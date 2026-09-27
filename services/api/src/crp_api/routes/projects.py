from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import ProjectCreate, ProjectOverview, ProjectPage, ProjectResponse
from crp_api.services import projects as project_service
from crp_core.db.models import Scan, Snapshot, Source
from crp_core.db.session import transaction

router = APIRouter(prefix="/projects", tags=["projects"], responses={401: {"model": ErrorResponse}})

_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse}}


def _workspace_not_found() -> ApiError:
    return ApiError(404, "workspace_not_found", "Workspace not found")


@router.post(
    "",
    status_code=201,
    response_model=ProjectResponse,
    responses={**_NOT_FOUND, 403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def create_project(
    body: ProjectCreate, principal: CurrentPrincipal, container: Container
) -> ProjectResponse:
    try:
        async with transaction(container.session_factory) as session:
            project = await project_service.create_project(
                session,
                principal,
                workspace_id=body.workspace_id,
                name=body.name,
                slug=body.slug,
                description=body.description,
            )
    except project_service.WorkspaceAccessError as exc:
        raise _workspace_not_found() from exc
    except project_service.InsufficientRoleError as exc:
        raise ApiError(403, "insufficient_role", "A member role is required") from exc
    except project_service.ProjectSlugConflictError as exc:
        raise ApiError(
            409,
            "project_slug_conflict",
            "A project with this slug already exists in the workspace",
            {"slug": str(exc)},
        ) from exc
    return ProjectResponse.model_validate(project, from_attributes=True)


@router.get("", response_model=ProjectPage, responses=_NOT_FOUND)
async def list_projects(
    principal: CurrentPrincipal,
    container: Container,
    workspace_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=project_service.MAX_PAGE_SIZE)] = 50,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> ProjectPage:
    try:
        async with transaction(container.session_factory) as session:
            page = await project_service.list_projects(
                session, principal, workspace_id=workspace_id, limit=limit, cursor=cursor
            )
    except project_service.WorkspaceAccessError as exc:
        raise _workspace_not_found() from exc
    except project_service.InvalidCursorError as exc:
        raise ApiError(400, "invalid_cursor", "The pagination cursor is malformed") from exc
    return ProjectPage(
        items=[ProjectResponse.model_validate(p, from_attributes=True) for p in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/{project_id}", response_model=ProjectResponse, responses=_NOT_FOUND)
async def get_project(
    project_id: UUID, principal: CurrentPrincipal, container: Container
) -> ProjectResponse:
    try:
        async with transaction(container.session_factory) as session:
            project = await project_service.get_project(session, principal, project_id)
    except project_service.ProjectNotFoundError as exc:
        raise ApiError(404, "project_not_found", "Project not found") from exc
    return ProjectResponse.model_validate(project, from_attributes=True)


@router.get("/{project_id}/overview", response_model=ProjectOverview, responses=_NOT_FOUND)
async def project_overview(
    project_id: UUID, principal: CurrentPrincipal, container: Container
) -> ProjectOverview:
    """Latest frozen snapshot and scan for dashboard cards (real counts only)."""
    from crp_api.routes.scans import _scan_response
    from crp_api.routes.snapshots import snapshot_response

    try:
        async with transaction(container.session_factory) as session:
            project = await project_service.get_project(session, principal, project_id)
            snapshot_row = (
                await session.execute(
                    select(Snapshot, Source)
                    .join(Source, Source.id == Snapshot.source_id)
                    .where(Snapshot.project_id == project.id)
                    .order_by(Snapshot.created_at.desc())
                    .limit(1)
                )
            ).first()
            scan = (
                await session.execute(
                    select(Scan)
                    .where(Scan.project_id == project.id)
                    .order_by(Scan.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            counts = (
                await session.execute(
                    select(
                        select(func.count())
                        .where(Snapshot.project_id == project.id)
                        .scalar_subquery(),
                        select(func.count()).where(Scan.project_id == project.id).scalar_subquery(),
                    )
                )
            ).one()
            return ProjectOverview(
                project=ProjectResponse.model_validate(project, from_attributes=True),
                latest_snapshot=snapshot_response(*snapshot_row) if snapshot_row else None,
                latest_scan=await _scan_response(session, scan) if scan else None,
                snapshot_count=counts[0],
                scan_count=counts[1],
            )
    except project_service.ProjectNotFoundError as exc:
        raise ApiError(404, "project_not_found", "Project not found") from exc
