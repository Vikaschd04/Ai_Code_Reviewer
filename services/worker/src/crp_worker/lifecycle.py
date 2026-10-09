"""Apply a finished scan to the project's durable issues (status + strict recheck state).

Only baseline scans (uploads, captures and the default branch's latest commit) of the project's
newest evaluated-or-newer snapshot that completed at least one engine update issues. Pull request
and comparison scans, scans of a commit whose review was superseded, older-snapshot and
failed/canceled scans only link their findings to existing issues. A file renamed with identical
content keeps its issues: they move to the new path (event ``moved``) instead of reappearing as
new issues. Present fingerprints become VERIFIED_PRESENT (RESOLVED ones reopen); absent
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

from crp_analysis.lifecycle import (
    Prior,
    RunView,
    anchor_key,
    classify_absence,
    observed_ruleset,
    rule_hashes,
)
from crp_analysis.sources.changes import diff_manifests
from crp_core.db.models import (
    CodeReview,
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
from crp_core.domain.states import (
    CodeReviewState,
    FileDisposition,
    FindingStatus,
    RecheckState,
    ScanMode,
    ScanState,
)

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
    if scan.mode != ScanMode.BASELINE.value:
        return "pull request and comparison reviews never change the project's issues"
    superseded = await session.scalar(
        select(CodeReview.id).where(
            CodeReview.head_scan_id == scan.id,
            (CodeReview.cancel_requested_at.is_not(None))
            | (CodeReview.state == CodeReviewState.SUPERSEDED.value),
        )
    )
    if superseded is not None:
        return "a newer commit of this branch is being reviewed; issues keep their state"
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


async def _manifest(session: AsyncSession, snapshot_id: UUID) -> dict[str, str]:
    rows = await session.execute(
        select(FileEntry.path, FileEntry.blob_sha256).where(
            FileEntry.snapshot_id == snapshot_id,
            FileEntry.disposition == FileDisposition.ANALYZABLE.value,
        )
    )
    return {path: sha for path, sha in rows.all() if sha}


def _slot_keys(rows: list[tuple[Finding, str]]) -> dict[tuple[object, ...], str]:
    """(path, engine, rule, line, column, n) -> fingerprint; n numbers identical locations."""
    seen: Counter[tuple[object, ...]] = Counter()
    keys: dict[tuple[object, ...], str] = {}
    for finding, path in sorted(
        rows,
        key=lambda r: (
            r[1],
            r[0].engine,
            r[0].rule_id,
            r[0].start_line or 0,
            r[0].start_column or 0,
        ),
    ):
        base = (path, finding.engine, finding.rule_id, finding.start_line, finding.start_column)
        keys[(*base, seen[base])] = finding.fingerprint
        seen[base] += 1
    return keys


async def _moves(
    session: AsyncSession,
    scan: Scan,
    findings: list[tuple[Finding, str]],
    issues: dict[str, Issue],
) -> dict[str, tuple[Issue, str]]:
    """Issues of files renamed with identical content since the last evaluated scan.

    Identical content gives identical findings at identical lines, so a finding in the new file
    takes over the issue of the same rule, line and column in the old file.
    """
    previous = (
        await session.execute(
            select(Scan)
            .where(
                Scan.project_id == scan.project_id,
                Scan.lifecycle_applied.is_(True),
                Scan.id != scan.id,
            )
            .order_by(Scan.finished_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if previous is None or previous.snapshot_id == scan.snapshot_id:
        return {}
    renames = diff_manifests(
        await _manifest(session, previous.snapshot_id), await _manifest(session, scan.snapshot_id)
    ).renames
    if not renames:
        return {}
    old_rows = [
        (finding, path)
        for finding, path in (
            await session.execute(
                select(Finding, FileEntry.path)
                .join(FileEntry, FileEntry.id == Finding.file_entry_id)
                .where(Finding.scan_id == previous.id, FileEntry.path.in_(set(renames.values())))
            )
        ).all()
    ]
    old_keys = _slot_keys(old_rows)
    new_rows = [(finding, path) for finding, path in findings if path in renames]
    moves: dict[str, tuple[Issue, str]] = {}
    for key, new_fp in _slot_keys(new_rows).items():
        path = str(key[0])
        old_fp = old_keys.get((renames[path], *key[1:]))
        issue = issues.get(old_fp) if old_fp else None
        if issue is not None and new_fp not in issues and new_fp != old_fp:
            moves[new_fp] = (issue, path)
    return moves


async def _anchor_moves(
    session: AsyncSession, findings: list[tuple[Finding, str]], issues: dict[str, Issue]
) -> dict[str, tuple[Issue, str]]:
    """Part-level findings (``details.anchor_key``, ADR 0020) follow their part: when the part's
    anchor file changes, its issue moves to the new file instead of being fixed and reopened."""
    present = {finding.fingerprint for finding, _ in findings}
    wanted: dict[tuple[str, str, str], tuple[str, str]] = {}
    for finding, path in findings:
        key = anchor_key(finding.details)
        if key is not None and finding.fingerprint not in issues:
            wanted[(finding.engine, finding.rule_id, key)] = (finding.fingerprint, path)
    rules = {(engine, rule) for engine, rule, _ in wanted}
    candidates = [
        issue
        for fp, issue in issues.items()
        if fp not in present and (issue.engine, issue.rule_id) in rules
    ]
    if not candidates:
        return {}
    keys: dict[UUID, str] = {}
    for issue_id, details in (
        await session.execute(
            select(Finding.issue_id, Finding.details)
            .where(Finding.issue_id.in_([issue.id for issue in candidates]))
            .order_by(Finding.created_at)
        )
    ).all():
        if (key := anchor_key(details)) is not None and issue_id is not None:
            keys[issue_id] = key  # the newest observation wins
    moves: dict[str, tuple[Issue, str]] = {}
    for issue in candidates:
        key = keys.get(issue.id)
        target = wanted.pop((issue.engine, issue.rule_id, key), None) if key else None
        if target is not None:
            moves[target[0]] = (issue, target[1])
    return moves


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
    events: list[IssueEvent] = []
    rows = [(finding, path) for finding, path in findings]
    for new_fp, (moved, new_path) in (await _moves(session, scan, rows, issues)).items():
        old_path = moved.path
        issues.pop(moved.fingerprint, None)
        moved.fingerprint, moved.path = new_fp, new_path
        issues[new_fp] = moved
        events.append(
            _event(moved, scan.id, "moved", "the file was renamed", path=[old_path, new_path])
        )
        counts["moved"] += 1
    for new_fp, (moved, new_path) in (await _anchor_moves(session, rows, issues)).items():
        old_path = moved.path
        issues.pop(moved.fingerprint, None)
        moved.fingerprint, moved.path = new_fp, new_path
        issues[new_fp] = moved
        events.append(
            _event(
                moved,
                scan.id,
                "moved",
                "the part's anchor file changed",
                path=[old_path, new_path],
            )
        )
        counts["moved"] += 1
    present: set[str] = set()
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
        issue.last_seen_ruleset_sha256 = observed_ruleset(
            finding.details, run.ruleset_sha256 if run else None
        )
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
                rule_hashes(r.diagnostics),
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
