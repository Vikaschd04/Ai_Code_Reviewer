"""Authorization-first lookups: every row is resolved through the caller's workspace grants."""

from __future__ import annotations

import base64
import binascii
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from crp_api.auth.principal import Principal
from crp_api.errors import ApiError
from crp_core.domain.states import MembershipRole


class WorkspaceScoped(Protocol):
    @property
    def workspace_id(self) -> UUID: ...


async def get_scoped[T: WorkspaceScoped](
    session: AsyncSession,
    principal: Principal,
    model: type[T],
    object_id: UUID,
    *,
    not_found: str,
    required: MembershipRole = MembershipRole.VIEWER,
    for_update: bool = False,
) -> T:
    """Load a workspace-scoped row; non-members get 404 (existence is not revealed)."""
    row = await session.get(model, object_id, with_for_update=for_update)
    if row is None or principal.role_in(row.workspace_id) is None:
        raise ApiError(404, not_found, f"{not_found.replace('_', ' ').capitalize()}")
    if not principal.has_role(row.workspace_id, required):
        raise ApiError(403, "insufficient_role", f"A {required.value} role is required")
    return row


def encode_offset(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()


def decode_offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    malformed = ApiError(400, "invalid_cursor", "The pagination cursor is malformed")
    try:
        text = base64.urlsafe_b64decode(cursor.encode()).decode()
        value = int(text[2:]) if text.startswith("o:") else -1
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise malformed from exc
    if value < 0:
        raise malformed
    return value
