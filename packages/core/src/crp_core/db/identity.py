"""Provisioning of the built-in identities: the token owner and the optional demo account.

Local-token mode maps the generated token to one well-known subject with owner membership of one
local workspace. When enabled, a shared demo account (no credentials) gets *member* access to a
separate demo workspace only, so it can never see the owner's projects. Both operations are
idempotent. Hosted identity providers (OIDC/SAML, planned for P07) provision users themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from crp_core.db.models import Membership, User, Workspace
from crp_core.domain.states import MembershipRole

LOCAL_SUBJECT = "local:developer"
LOCAL_DISPLAY_NAME = "Local developer"
LOCAL_WORKSPACE_SLUG = "local"
LOCAL_WORKSPACE_NAME = "Local workspace"
DEMO_SUBJECT = "demo:guest"
DEMO_DISPLAY_NAME = "Demo user"
DEMO_WORKSPACE_SLUG = "demo"
DEMO_WORKSPACE_NAME = "Demo workspace"


@dataclass(frozen=True, slots=True)
class LocalIdentity:
    user_id: UUID
    workspace_id: UUID


async def _ensure_identity(
    session: AsyncSession,
    *,
    subject: str,
    display_name: str,
    workspace_slug: str,
    workspace_name: str,
    role: MembershipRole,
) -> LocalIdentity:
    await session.execute(
        insert(Workspace)
        .values(slug=workspace_slug, name=workspace_name)
        .on_conflict_do_nothing(index_elements=[Workspace.slug])
    )
    await session.execute(
        insert(User)
        .values(subject=subject, display_name=display_name)
        .on_conflict_do_nothing(index_elements=[User.subject])
    )
    workspace_id = (
        await session.execute(select(Workspace.id).where(Workspace.slug == workspace_slug))
    ).scalar_one()
    user_id = (await session.execute(select(User.id).where(User.subject == subject))).scalar_one()
    await session.execute(
        insert(Membership)
        .values(workspace_id=workspace_id, user_id=user_id, role=role.value)
        .on_conflict_do_nothing(index_elements=[Membership.workspace_id, Membership.user_id])
    )
    return LocalIdentity(user_id=user_id, workspace_id=workspace_id)


async def ensure_local_identity(session: AsyncSession) -> LocalIdentity:
    """Create (or find) the local user, workspace and owner membership inside ``session``."""
    return await _ensure_identity(
        session,
        subject=LOCAL_SUBJECT,
        display_name=LOCAL_DISPLAY_NAME,
        workspace_slug=LOCAL_WORKSPACE_SLUG,
        workspace_name=LOCAL_WORKSPACE_NAME,
        role=MembershipRole.OWNER,
    )


async def ensure_demo_identity(session: AsyncSession) -> LocalIdentity:
    """Create (or find) the demo user with member access to the demo workspace only."""
    return await _ensure_identity(
        session,
        subject=DEMO_SUBJECT,
        display_name=DEMO_DISPLAY_NAME,
        workspace_slug=DEMO_WORKSPACE_SLUG,
        workspace_name=DEMO_WORKSPACE_NAME,
        role=MembershipRole.MEMBER,
    )
