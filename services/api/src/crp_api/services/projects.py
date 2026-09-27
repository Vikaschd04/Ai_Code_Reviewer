"""Project management with workspace-scoped authorization applied before every query."""

from __future__ import annotations

import base64
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from crp_api.auth.principal import Principal
from crp_core.db.models import Project
from crp_core.domain.states import MembershipRole, ProjectOrigin

MAX_PAGE_SIZE = 100


class ProjectNotFoundError(LookupError):
    pass


class WorkspaceAccessError(PermissionError):
    """The principal has no (or insufficient) access; callers must not reveal existence."""


class InsufficientRoleError(PermissionError):
    """The principal can see the workspace but lacks the role required for this action."""


class ProjectSlugConflictError(ValueError):
    pass


class InvalidCursorError(ValueError):
    pass


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64].strip("-")
    return slug or "project"


def _encode_cursor(created_at: datetime, project_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{project_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        created_raw, id_raw = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(created_raw), uuid.UUID(id_raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidCursorError("cursor is malformed") from exc


@dataclass(frozen=True, slots=True)
class ProjectPageResult:
    items: list[Project]
    next_cursor: str | None


async def create_project(
    session: AsyncSession,
    principal: Principal,
    *,
    workspace_id: uuid.UUID,
    name: str,
    slug: str | None,
    description: str,
    origin: ProjectOrigin = ProjectOrigin.USER,
) -> Project:
    if principal.role_in(workspace_id) is None:
        raise WorkspaceAccessError(workspace_id)
    if not principal.has_role(workspace_id, MembershipRole.MEMBER):
        raise InsufficientRoleError(MembershipRole.MEMBER)
    final_slug = slug or slugify(name)
    inserted = await session.execute(
        insert(Project)
        .values(
            workspace_id=workspace_id,
            slug=final_slug,
            name=name,
            description=description,
            origin=origin.value,
            created_by=principal.user_id,
        )
        .on_conflict_do_nothing(index_elements=[Project.workspace_id, Project.slug])
        .returning(Project.id)
    )
    project_id = inserted.scalar_one_or_none()
    if project_id is None:
        raise ProjectSlugConflictError(final_slug)
    return (await session.execute(select(Project).where(Project.id == project_id))).scalar_one()


def _visible_projects(principal: Principal) -> Select[Project]:
    return select(Project).where(Project.workspace_id.in_(principal.workspace_ids))


async def list_projects(
    session: AsyncSession,
    principal: Principal,
    *,
    workspace_id: uuid.UUID | None,
    limit: int,
    cursor: str | None,
) -> ProjectPageResult:
    query = _visible_projects(principal)
    if workspace_id is not None:
        if principal.role_in(workspace_id) is None:
            raise WorkspaceAccessError(workspace_id)
        query = query.where(Project.workspace_id == workspace_id)
    if cursor is not None:
        created_at, last_id = _decode_cursor(cursor)
        query = query.where(
            or_(
                Project.created_at > created_at,
                and_(Project.created_at == created_at, Project.id > last_id),
            )
        )
    page_size = max(1, min(limit, MAX_PAGE_SIZE))
    rows = (
        (await session.execute(query.order_by(Project.created_at, Project.id).limit(page_size + 1)))
        .scalars()
        .all()
    )
    items = list(rows[:page_size])
    next_cursor = (
        _encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > page_size else None
    )
    return ProjectPageResult(items=items, next_cursor=next_cursor)


async def get_project(
    session: AsyncSession, principal: Principal, project_id: uuid.UUID
) -> Project:
    project = (
        await session.execute(_visible_projects(principal).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise ProjectNotFoundError(project_id)
    return project
