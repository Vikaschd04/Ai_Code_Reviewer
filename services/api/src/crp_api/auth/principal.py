"""Platform principal: an authenticated subject resolved to a user and workspace grants."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crp_api.auth.provider import AuthMethod
from crp_core.db.identity import DEMO_SUBJECT
from crp_core.db.models import Membership, User, Workspace
from crp_core.domain.states import MembershipRole


@dataclass(frozen=True, slots=True)
class WorkspaceGrant:
    workspace_id: UUID
    slug: str
    name: str
    role: MembershipRole


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    subject: str
    display_name: str
    auth_method: AuthMethod
    grants: tuple[WorkspaceGrant, ...]

    def role_in(self, workspace_id: UUID) -> MembershipRole | None:
        for grant in self.grants:
            if grant.workspace_id == workspace_id:
                return grant.role
        return None

    def has_role(self, workspace_id: UUID, required: MembershipRole) -> bool:
        role = self.role_in(workspace_id)
        return role is not None and role.at_least(required)

    @property
    def workspace_ids(self) -> tuple[UUID, ...]:
        return tuple(grant.workspace_id for grant in self.grants)

    @property
    def is_demo(self) -> bool:
        return self.subject == DEMO_SUBJECT

    @property
    def is_operator(self) -> bool:
        """Deployment diagnostics require admin or owner in at least one workspace."""
        return any(grant.role.at_least(MembershipRole.ADMIN) for grant in self.grants)


class PrincipalNotProvisionedError(LookupError):
    pass


class PrincipalDisabledError(PermissionError):
    pass


async def load_principal(session: AsyncSession, subject: str, method: AuthMethod) -> Principal:
    user = (await session.execute(select(User).where(User.subject == subject))).scalar_one_or_none()
    if user is None:
        raise PrincipalNotProvisionedError(subject)
    if user.disabled_at is not None:
        raise PrincipalDisabledError(subject)
    rows = await session.execute(
        select(Workspace.id, Workspace.slug, Workspace.name, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id, Membership.revoked_at.is_(None))
        .order_by(Workspace.slug)
    )
    grants = tuple(
        WorkspaceGrant(workspace_id=ws_id, slug=slug, name=name, role=MembershipRole(role))
        for ws_id, slug, name, role in rows.all()
    )
    return Principal(
        user_id=user.id,
        subject=user.subject,
        display_name=user.display_name,
        auth_method=method,
        grants=grants,
    )
