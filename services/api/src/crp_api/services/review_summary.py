"""Gather a fix workspace's reviewer summary (ADD-A5): its changes with the recorded reasons, the
latest check and whether it covers the current content, and the impact on the rest of the upload
from the upload's dependency map. The wording lives in ``crp_analysis.fixes.review_summary``."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crp_analysis.fixes import review_summary as summary
from crp_api.services import architecture
from crp_api.services import change_sets as workspace
from crp_core.db.models import (
    ChangeSet,
    ChangeSetCheck,
    ChangeSetEvent,
    GraphBuild,
    Snapshot,
    Source,
)
from crp_core.domain.states import ChangeSource, GraphBuildState

_REASON_SOURCES = {ChangeSource.MANUAL.value, ChangeSource.RECIPE.value, ChangeSource.AI.value}


@dataclass(frozen=True, slots=True)
class Gathered:
    changes: list[summary.FileChange]
    check: summary.CheckState
    impact: summary.Impact
    needs_human: list[str]
    markdown: str


async def _reasons(session: AsyncSession, change_set_id: object) -> dict[str, list[str]]:
    """Per path, the recorded change summaries since the file was last back at the upload."""
    rows = await session.execute(
        select(ChangeSetEvent.path, ChangeSetEvent.source, ChangeSetEvent.summary)
        .where(ChangeSetEvent.change_set_id == change_set_id, ChangeSetEvent.path.is_not(None))
        .order_by(ChangeSetEvent.created_at, ChangeSetEvent.id)
    )
    reasons: dict[str, list[str]] = {}
    for path, source, text in rows.all():
        if path is None:
            continue
        if source == ChangeSource.REVERT.value:
            reasons.pop(path, None)
        elif source in _REASON_SOURCES and text:
            reasons.setdefault(path, []).append(text)
    return reasons


async def gather(session: AsyncSession, change_set: ChangeSet) -> Gathered:
    files = await workspace.files_of(session, change_set.id)
    reasons = await _reasons(session, change_set.id)
    changes = [
        summary.FileChange(
            f.path,
            f.action,
            tuple(f.sources),
            tuple(f.flags),
            tuple(dict.fromkeys(reasons.get(f.path, []))),
        )
        for f in files
    ]
    latest = await session.scalar(
        select(ChangeSetCheck)
        .where(ChangeSetCheck.change_set_id == change_set.id)
        .order_by(ChangeSetCheck.created_at.desc())
        .limit(1)
    )
    result = (latest.result or {}) if latest else {}
    raw = result.get("counts")
    counts = (
        {str(k): v for k, v in raw.items() if isinstance(v, int)} if isinstance(raw, dict) else {}
    )
    check = summary.CheckState(
        checked=latest is not None and latest.state in {"SUCCEEDED", "PARTIAL"},
        current=latest is not None and latest.content_sha256 == change_set.content_sha256,
        state=latest.state if latest else None,
        counts=counts,
    )
    build = (
        await session.execute(
            select(GraphBuild).where(
                GraphBuild.snapshot_id == change_set.base_snapshot_id,
                GraphBuild.is_current.is_(True),
            )
        )
    ).scalar_one_or_none()
    model = (
        await architecture.load_model(session, build)
        if build is not None and build.state != GraphBuildState.FAILED.value
        else None
    )
    found = summary.impact(model, [f.path for f in files])
    todo = summary.needs_human(changes, check, found)
    snapshot = await session.get(Snapshot, change_set.base_snapshot_id)
    source = await session.get(Source, snapshot.source_id) if snapshot else None
    upload = source.display_name if source else "the upload"
    text = summary.markdown(change_set.title, upload, changes, check, found, todo)
    return Gathered(changes, check, found, todo, text)
