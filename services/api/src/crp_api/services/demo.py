"""Usage quotas for the shared demo account (only its own demo workspace is affected)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crp_api.errors import ApiError
from crp_core.config import Settings
from crp_core.db.models import Project, Scan


def _limit_reached(message: str, limit: int) -> ApiError:
    return ApiError(429, "demo_limit_reached", message, {"limit": limit})


async def enforce_project_quota(
    session: AsyncSession, workspace_id: UUID, settings: Settings
) -> None:
    count = await session.scalar(
        select(func.count()).select_from(Project).where(Project.workspace_id == workspace_id)
    )
    if (count or 0) >= settings.demo_max_projects:
        raise _limit_reached(
            f"The demo workspace already has {settings.demo_max_projects} projects; "
            "open an existing project instead",
            settings.demo_max_projects,
        )


async def enforce_scan_quota(session: AsyncSession, workspace_id: UUID, settings: Settings) -> None:
    since = datetime.now(UTC) - timedelta(hours=1)
    count = await session.scalar(
        select(func.count())
        .select_from(Scan)
        .where(Scan.workspace_id == workspace_id, Scan.created_at >= since)
    )
    if (count or 0) >= settings.demo_max_scans_per_hour:
        raise _limit_reached(
            f"The demo allows {settings.demo_max_scans_per_hour} scans per hour; try again later",
            settings.demo_max_scans_per_hour,
        )
