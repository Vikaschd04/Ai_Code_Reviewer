"""Provisioning of the single local-development identity.

Local-token mode maps the generated token to one well-known subject with owner membership of one
local workspace. The operation is idempotent so migrate/bootstrap can run it repeatedly. Hosted
identity providers (OIDC/SAML, planned for P07) will provision users through their own flow.
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


@dataclass(frozen=True, slots=True)
class LocalIdentity:
    user_id: UUID
    workspace_id: UUID


async def ensure_local_identity(session: AsyncSession) -> LocalIdentity:
    """Create (or find) the local user, workspace and owner membership inside ``session``."""
    await session.execute(
        insert(Workspace)
        .values(slug=LOCAL_WORKSPACE_SLUG, name=LOCAL_WORKSPACE_NAME)
        .on_conflict_do_nothing(index_elements=[Workspace.slug])
    )
    await session.execute(
        insert(User)
        .values(subject=LOCAL_SUBJECT, display_name=LOCAL_DISPLAY_NAME)
        .on_conflict_do_nothing(index_elements=[User.subject])
    )
    workspace_id = (
        await session.execute(select(Workspace.id).where(Workspace.slug == LOCAL_WORKSPACE_SLUG))
    ).scalar_one()
    user_id = (
        await session.execute(select(User.id).where(User.subject == LOCAL_SUBJECT))
    ).scalar_one()
    await session.execute(
        insert(Membership)
        .values(workspace_id=workspace_id, user_id=user_id, role=MembershipRole.OWNER.value)
        .on_conflict_do_nothing(index_elements=[Membership.workspace_id, Membership.user_id])
    )
    return LocalIdentity(user_id=user_id, workspace_id=workspace_id)
