"""Apply a finished scan to the project's durable issues (status + strict recheck state).

Only scans of the project's newest evaluated-or-newer snapshot that completed at least one
engine update issues; older-snapshot or failed/canceled scans only link their findings to
existing issues. Present fingerprints become VERIFIED_PRESENT (RESOLVED ones reopen); absent
ones are classified by ``crp_analysis.lifecycle.classify_absence`` and are RESOLVED only on a
VERIFIED_ABSENT recheck. Every change is written to ``issue_events``. Project rows are locked so
concurrent finalizations of one project serialize.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from crp_analysis.lifecycle import Prior, RunView, classify_absence
from crp_core.db.models import (
    EngineRun,
    FileCoverage,
    FileEntry,
    Finding,
    Issue,
    IssueEvent,
    Project,
    Scan,
    Snapshot,
)
from crp_core.domain.states import FileDisposition, FindingStatus, RecheckState, ScanState

_AUTO_RESOLVABLE = frozenset(
    {FindingStatus.OPEN.value, FindingStatus.TRIAGED.value, FindingStatus.FIX_PROPOSED.value}
)


def _event(
    issue: Issue, scan_id: UUID, kind: str, reason: str | None, **changes: object
) -> IssueEvent:
    return IssueEvent(
        issue_id=issue.id,
        scan_id=scan_id,
        actor_kind="system",
        kind=kind,
        changes=changes,
        reason=reason,
    )


async def _link(session: AsyncSession, scan: Scan) -> None:
    await session.execute(
        update(Finding)
        .where(
            Finding.scan_id == scan.id,
            Issue.project_id == Finding.project_id,
            Issue.fingerprint == Finding.fingerprint,
        )
        .values(issue_id=Issue.id)
    )


async def _applicable(session: AsyncSession, scan: Scan, final: ScanState) -> str | None:
    """None when the lifecycle applies, else the reason it does not."""
    if final not in {ScanState.SUCCEEDED, ScanState.PARTIAL}:
        return f"scan ended {final.value}; issues were not re-evaluated"
    newest = (
        await session.execute(
            select(func.max(Snapshot.frozen_at))
            .join(Scan, Scan.snapshot_id == Snapshot.id)
            .where(Scan.project_id == scan.project_id, Scan.lifecycle_applied.is_(True))
        )
    ).scalar_one_or_none()
    snapshot = await session.get(Snapshot, scan.snapshot_id)
    if (
        newest is not None
        and snapshot is not None
        and snapshot.frozen_at is not None
        and snapshot.frozen_at < newest
    ):
        return "a newer snapshot has already been evaluated; issues keep their newer state"
    return None


async def apply_lifecycle(
    session: AsyncSession, scan: Scan, final: ScanState, runs: list[EngineRun]
) -> dict[str, object]:
    await session.execute(select(Project.id).where(Project.id == scan.project_id).with_for_update())
    reason = await _applicable(session, scan, final)
    if reason is not None:
        await _link(session, scan)
        return {"applied": False, "reason": reason}

    now = datetime.now(UTC)
    counts: Counter[str] = Counter()
    by_engine = {r.engine: r for r in runs}
    findings = (
        await session.execute(
            select(Finding, FileEntry.path)
            .join(FileEntry, FileEntry.id == Finding.file_entry_id)
            .where(Finding.scan_id == scan.id)
        )
    ).all()
    issues = {
        i.fingerprint: i
        for i in (
            await session.execute(select(Issue).where(Issue.project_id == scan.project_id))
        ).scalars()
    }
    present: set[str] = set()
    events: list[IssueEvent] = []
    for finding, path in findings:
        present.add(finding.fingerprint)
        run = by_engine.get(finding.engine)
        issue = issues.get(finding.fingerprint)
        if issue is None:
            issue = Issue(
                id=uuid.uuid4(),
                workspace_id=scan.workspace_id,
                project_id=scan.project_id,
                fingerprint=finding.fingerprint,
                engine=finding.engine,
                rule_id=finding.rule_id,
                path=path,
                title=finding.title,
                severity=finding.severity,
                category=finding.category,
                status=FindingStatus.OPEN.value,
                recheck_state=RecheckState.VERIFIED_PRESENT.value,
                recheck_reason="reported by this scan",
                first_seen_scan_id=scan.id,
            )
            session.add(issue)
            events.append(_event(issue, scan.id, "created", None, status=[None, "OPEN"]))
            issues[finding.fingerprint] = issue
            counts["new"] += 1
        else:
            counts["present"] += 1
            if issue.status == FindingStatus.RESOLVED.value:
                events.append(
                    _event(
                        issue, scan.id, "reopened", "reported again", status=["RESOLVED", "OPEN"]
                    )
                )
                issue.status = FindingStatus.OPEN.value
                counts["reopened"] += 1
            if issue.recheck_state != RecheckState.VERIFIED_PRESENT.value:
                events.append(
                    _event(
                        issue,
                        scan.id,
                        "recheck_changed",
                        "reported by this scan",
                        recheck_state=[issue.recheck_state, "VERIFIED_PRESENT"],
                    )
                )
            issue.recheck_state = RecheckState.VERIFIED_PRESENT.value
            issue.recheck_reason = "reported by this scan"
            issue.title, issue.severity, issue.category = (
                finding.title,
                finding.severity,
                finding.category,
            )
        issue.last_seen_scan_id = scan.id
        issue.last_seen_at = now
        issue.last_seen_engine_version = finding.engine_version
        issue.last_seen_ruleset_sha256 = run.ruleset_sha256 if run else None
        issue.last_evaluated_scan_id = scan.id

    absent = [
        i
        for fp, i in issues.items()
        if fp not in present and i.status != FindingStatus.RESOLVED.value
    ]
    if absent:
        entries = {
            path: entry_id
            for entry_id, path in (
                await session.execute(
                    select(FileEntry.id, FileEntry.path).where(
                        FileEntry.snapshot_id == scan.snapshot_id,
                        FileEntry.disposition == FileDisposition.ANALYZABLE.value,
                    )
                )
            ).all()
        }
        coverage = {
            (run_id, entry_id): outcome
            for run_id, entry_id, outcome in (
                await session.execute(
                    select(
                        FileCoverage.engine_run_id,
                        FileCoverage.file_entry_id,
                        FileCoverage.outcome,
                    ).where(FileCoverage.engine_run_id.in_([r.id for r in runs]))
                )
            ).all()
        }
        views = {
            r.engine: RunView(
                r.engine,
                r.state,
                r.engine_version,
                r.ruleset_sha256,
                frozenset(r.enabled_rules) if r.enabled_rules is not None else None,
            )
            for r in runs
        }
        for issue in absent:
            run = by_engine.get(issue.engine)
            entry_id = entries.get(issue.path)
            result = classify_absence(
                Prior(
                    issue.engine,
                    issue.rule_id,
                    issue.path,
                    issue.last_seen_engine_version,
                    issue.last_seen_ruleset_sha256,
                ),
                views.get(issue.engine),
                file_present=entry_id is not None,
                file_outcome=coverage.get((run.id, entry_id)) if run and entry_id else None,
            )
            counts[result.state.value.lower()] += 1
            if issue.recheck_state != result.state.value:
                events.append(
                    _event(
                        issue,
                        scan.id,
                        "recheck_changed",
                        result.reason,
                        recheck_state=[issue.recheck_state, result.state.value],
                    )
                )
            issue.recheck_state = result.state.value
            issue.recheck_reason = result.reason
            issue.last_evaluated_scan_id = scan.id
            if result.state is RecheckState.VERIFIED_ABSENT and issue.status in _AUTO_RESOLVABLE:
                events.append(
                    _event(
                        issue, scan.id, "resolved", result.reason, status=[issue.status, "RESOLVED"]
                    )
                )
                issue.status = FindingStatus.RESOLVED.value
                counts["resolved"] += 1

    for issue in issues.values():
        if (
            issue.status == FindingStatus.ACCEPTED_RISK.value
            and issue.exception_expires_at is not None
            and issue.exception_expires_at <= now
        ):
            events.append(
                _event(
                    issue,
                    scan.id,
                    "exception_expired",
                    f"accepted-risk exception expired at {issue.exception_expires_at.isoformat()}",
                    status=["ACCEPTED_RISK", "OPEN"],
                )
            )
            issue.status = FindingStatus.OPEN.value
            counts["exceptions_expired"] += 1
    await session.flush()  # issues first: events reference them
    session.add_all(events)
    await session.flush()
    await _link(session, scan)
    scan.lifecycle_applied = True
    return {"applied": True, **dict(counts)}
