"""Fix-workspace checks (P08): the workspace's edits applied to a derived snapshot, scanned with
per-file reuse, and compared with the upload's findings.

The derived snapshot is a new, immutable snapshot (``derived_from`` = the upload,
``change_set_id`` = the workspace). It is built from the file revisions recorded when the check was
requested, so edits made meanwhile never mix into a running check. Its scan has mode
``change_set``: it never changes the project's issues.

Outcome of each upload finding:
- fixed: a compatible verified absence;
- still present;
- suppressed: gone only because a suppression marker was added in that file;
- not rechecked: file removed, check incomplete, or rules changed.

New findings introduced by the edits are listed. No project code is executed.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ChildWorkflowError, is_cancelled_exception
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    import uuid

    from sqlalchemy import select, update
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from crp_analysis.fixes.changeset import FileChange, RevisionInfo, derive_entries
    from crp_analysis.inventory import build_inventory
    from crp_analysis.manifest import ManifestEntry, blob_key, manifest_digest, manifest_document
    from crp_analysis.policy import POLICY_VERSION
    from crp_analysis.sources.changes import diff_findings
    from crp_core.artifacts import ArtifactKey, ArtifactStore
    from crp_core.config import Settings
    from crp_core.db.models import (
        ChangeSet,
        ChangeSetCheck,
        FileEntry,
        Scan,
        Snapshot,
    )
    from crp_core.db.session import transaction
    from crp_core.domain.states import (
        CacheMode,
        CaptureStatus,
        ChangeAction,
        ChangeSetCheckState,
        FileDisposition,
        IssueOutcome,
        ScanMode,
        ScanState,
    )
    from crp_core.workflows.contracts import (
        CHANGE_SET_CHECK_WORKFLOW_NAME,
        SCAN_POLICY,
        SCAN_WORKFLOW_NAME,
        ChangeSetCheckFinalize,
        ChangeSetCheckInput,
        ChangeSetCheckPlan,
        ChangeSetCheckResult,
        ScanWorkflowInput,
        ScanWorkflowResult,
        scan_workflow_id,
    )
    from crp_worker import comparison
    from crp_worker.progress import heartbeat

logger = logging.getLogger(__name__)
_DONE = (ScanState.SUCCEEDED.value, ScanState.PARTIAL.value)
_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_NEW_ITEMS = 200
_INVENTORY_BYTES = 1024 * 1024


def _text(value: object) -> str | None:
    return str(value) if value else None


def _number(value: object) -> int | None:
    return value if isinstance(value, int) else None


def revision_of(item: dict[str, object]) -> RevisionInfo:
    """A revision recorded on the check (``files`` JSON) as the derived manifest needs it."""
    return RevisionInfo(
        FileChange(
            str(item["path"]),
            ChangeAction(str(item["action"])),
            _text(item.get("base_sha256")),
            _text(item.get("sha256")),
        ),
        _number(item.get("size_bytes")),
        _number(item.get("line_count")),
    )


def flags_of(item: dict[str, object]) -> list[str]:
    flags = item.get("flags")
    return [str(flag) for flag in flags] if isinstance(flags, list) else []


class ChangeSetCheckActivities:
    def __init__(
        self,
        settings: Settings,
        store: ArtifactStore,
        sessions: async_sessionmaker[AsyncSession],
    ) -> None:
        self._settings = settings
        self._store = store
        self._sessions = sessions

    async def _end(
        self,
        check_id: UUID,
        state: ChangeSetCheckState,
        *,
        code: str | None = None,
        message: str | None = None,
    ) -> ChangeSetCheckResult:
        async with transaction(self._sessions) as session:
            check = await session.get(ChangeSetCheck, check_id, with_for_update=True)
            if check is None:
                return ChangeSetCheckResult(check_id=check_id, state="MISSING")
            if ChangeSetCheckState(check.state).is_terminal:
                return ChangeSetCheckResult(check_id=check_id, state=check.state)
            check.state = state.value
            check.finished_at = datetime.now(UTC)
            if code is not None:
                check.error_code, check.error_message = code, message
        return ChangeSetCheckResult(check_id=check_id, state=state.value)

    # -- prepare --------------------------------------------------------------------------------

    @activity.defn(name="cscheck.prepare")
    async def prepare(self, payload: ChangeSetCheckInput) -> ChangeSetCheckPlan:
        check_id = payload.check_id
        async with transaction(self._sessions) as session:
            check = await session.get(ChangeSetCheck, check_id, with_for_update=True)
            if check is None or ChangeSetCheckState(check.state).is_terminal:
                return ChangeSetCheckPlan(check_id=check_id, terminal=True)
            if check.cancel_requested_at is not None:
                check.state = ChangeSetCheckState.CANCELED.value
                check.finished_at = datetime.now(UTC)
                return ChangeSetCheckPlan(check_id=check_id, terminal=True)
            change_set = await session.get(ChangeSet, check.change_set_id)
            if change_set is None:
                return ChangeSetCheckPlan(check_id=check_id, terminal=True)
            base = await session.get(Snapshot, change_set.base_snapshot_id)
            if base is None:
                check.state = ChangeSetCheckState.FAILED.value
                check.error_code, check.error_message = "base_missing", "The upload was deleted."
                check.finished_at = datetime.now(UTC)
                return ChangeSetCheckPlan(check_id=check_id, terminal=True)
            check.state = ChangeSetCheckState.RUNNING.value
            check.started_at = check.started_at or datetime.now(UTC)
            revisions = [revision_of(item) for item in check.files]
            change_set_id, base_id = change_set.id, base.id
            requested_by, workspace_id = check.requested_by, change_set.workspace_id
            project_id = change_set.project_id
        heartbeat("building the changed copy")
        snapshot_id = await self._derived_snapshot(change_set_id, base_id, revisions)
        runs: list[UUID] = []
        async with transaction(self._sessions) as session:
            check = await session.get(ChangeSetCheck, check_id, with_for_update=True)
            change_set = await session.get(ChangeSet, change_set_id)
            if check is None or change_set is None:
                return ChangeSetCheckPlan(check_id=check_id, terminal=True)
            base_scan = await session.scalar(
                select(Scan.id)
                .where(Scan.snapshot_id == base_id, Scan.state.in_(_DONE))
                .order_by(Scan.created_at.desc())
                .limit(1)
            )
            if change_set.base_scan_id is not None:
                pinned = await session.get(Scan, change_set.base_scan_id)
                if pinned is not None and pinned.state in _DONE:
                    base_scan = pinned.id
            if base_scan is None:
                base_scan = await self._scan(
                    session,
                    workspace_id,
                    project_id,
                    base_id,
                    ScanMode.REFERENCE,
                    f"cscheck-{check_id.hex}-base",
                    requested_by,
                )
                runs.append(base_scan)
            head_scan = await self._scan(
                session,
                workspace_id,
                project_id,
                snapshot_id,
                ScanMode.CHANGE_SET,
                f"cscheck-{check_id.hex}-head",
                requested_by,
            )
            runs.append(head_scan)
            check.snapshot_id, check.scan_id, check.base_scan_id = snapshot_id, head_scan, base_scan
            states = dict(
                (await session.execute(select(Scan.id, Scan.state).where(Scan.id.in_(runs)))).all()
            )
        pending = [s for s in runs if not ScanState(states.get(s, "QUEUED")).is_terminal]
        return ChangeSetCheckPlan(check_id=check_id, scan_ids=pending)

    async def _scan(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        project_id: UUID,
        snapshot_id: UUID,
        mode: ScanMode,
        key: str,
        requested_by: UUID | None,
    ) -> UUID:
        scan_id = (
            await session.execute(
                insert(Scan)
                .values(
                    id=uuid.uuid4(),
                    workspace_id=workspace_id,
                    project_id=project_id,
                    snapshot_id=snapshot_id,
                    mode=mode.value,
                    policy_version=SCAN_POLICY,
                    idempotency_key=key,
                    state=ScanState.QUEUED.value,
                    cache_mode=CacheMode.USE.value,
                    requested_by=requested_by,
                )
                .on_conflict_do_nothing(index_elements=[Scan.project_id, Scan.idempotency_key])
                .returning(Scan.id)
            )
        ).scalar_one_or_none()
        if scan_id is None:
            scan_id = (
                await session.execute(
                    select(Scan.id).where(
                        Scan.project_id == project_id, Scan.idempotency_key == key
                    )
                )
            ).scalar_one()
        await session.execute(
            update(Scan).where(Scan.id == scan_id).values(workflow_id=scan_workflow_id(scan_id))
        )
        return scan_id

    async def _derived_snapshot(
        self, change_set_id: UUID, base_id: UUID, revisions: list[RevisionInfo]
    ) -> UUID:
        async with transaction(self._sessions) as session:
            base = await session.get(Snapshot, base_id)
            if base is None:
                raise RuntimeError("the upload disappeared")
            rows = list(
                (
                    await session.execute(select(FileEntry).where(FileEntry.snapshot_id == base_id))
                ).scalars()
            )
            base_entries = [
                ManifestEntry(
                    r.path,
                    FileDisposition(r.disposition),
                    r.reason,
                    r.size_bytes,
                    r.blob_sha256,
                    r.language,
                    r.category,
                    r.line_count,
                )
                for r in rows
            ]
            entries = derive_entries(base_entries, revisions)
            digest = manifest_digest(entries)
            existing = await session.scalar(
                select(Snapshot.id).where(
                    Snapshot.change_set_id == change_set_id, Snapshot.manifest_sha256 == digest
                )
            )
            if existing is not None:
                return existing
            texts: dict[str, str] = {}
            for entry in entries:
                if (
                    entry.category == "build"
                    and entry.sha256
                    and (entry.size_bytes or 0) <= _INVENTORY_BYTES
                ):
                    data = await asyncio.to_thread(
                        self._store.read_bytes,
                        ArtifactKey(blob_key(entry.sha256)),
                        max_bytes=_INVENTORY_BYTES,
                    )
                    texts[entry.path] = data.decode("utf-8", errors="replace")
            snapshot = Snapshot(
                workspace_id=base.workspace_id,
                project_id=base.project_id,
                source_id=base.source_id,
                capture_status=CaptureStatus.FROZEN.value,
                manifest_sha256=digest,
                file_count=len(entries),
                total_bytes=sum(e.size_bytes or 0 for e in entries),
                frozen_at=datetime.now(UTC),
                policy_version=POLICY_VERSION,
                analyzable_count=sum(e.disposition is FileDisposition.ANALYZABLE for e in entries),
                excluded_count=sum(e.disposition is FileDisposition.EXCLUDED for e in entries),
                inventory=build_inventory(entries, texts),
                derived_from=base_id,
                change_set_id=change_set_id,
            )
            session.add(snapshot)
            await session.flush()
            key = ArtifactKey(f"snapshots/{snapshot.id.hex}/manifest.json")
            document = manifest_document(entries, policy_version=POLICY_VERSION)
            await asyncio.to_thread(self._store.put_bytes, key, document, overwrite=True)
            snapshot.manifest_key = str(key)
            session.add_all(
                FileEntry(
                    workspace_id=base.workspace_id,
                    project_id=base.project_id,
                    snapshot_id=snapshot.id,
                    path=e.path,
                    disposition=e.disposition.value,
                    reason=e.reason,
                    size_bytes=e.size_bytes,
                    blob_sha256=e.sha256 if e.disposition is FileDisposition.ANALYZABLE else None,
                    language=e.language,
                    category=e.category,
                    line_count=e.line_count,
                )
                for e in entries
            )
            return snapshot.id

    # -- complete -------------------------------------------------------------------------------

    @activity.defn(name="cscheck.complete")
    async def complete(self, payload: ChangeSetCheckInput) -> ChangeSetCheckResult:
        check_id = payload.check_id
        async with transaction(self._sessions) as session:
            check = await session.get(ChangeSetCheck, check_id)
            if check is None or ChangeSetCheckState(check.state).is_terminal:
                return ChangeSetCheckResult(
                    check_id=check_id, state=check.state if check else "MISSING"
                )
            if check.cancel_requested_at is not None:
                return await self._end(check_id, ChangeSetCheckState.CANCELED)
            head = await session.get(Scan, check.scan_id) if check.scan_id else None
            base = await session.get(Scan, check.base_scan_id) if check.base_scan_id else None
            if head is None or head.state not in _DONE:
                state = head.state if head else "missing"
                return await self._end(
                    check_id,
                    ChangeSetCheckState.FAILED,
                    code="scan_failed",
                    message=(head.error_message if head else None)
                    or f"The check of the changed copy ended {state}.",
                )
            heartbeat("comparing results")
            result = await self._compare(session, check, head, base)
        async with transaction(self._sessions) as session:
            row = await session.get(ChangeSetCheck, check_id, with_for_update=True)
            if row is None or ChangeSetCheckState(row.state).is_terminal:
                return ChangeSetCheckResult(
                    check_id=check_id, state=row.state if row else "MISSING"
                )
            row.result = result
            row.state = str(result["state"])
            row.finished_at = datetime.now(UTC)
            return ChangeSetCheckResult(check_id=check_id, state=row.state)

    async def _compare(
        self, session: AsyncSession, check: ChangeSetCheck, head: Scan, base: Scan | None
    ) -> dict[str, Any]:
        base_ok = base is not None and base.state in _DONE
        head_findings = await comparison.findings_of(session, head.id)
        base_findings = await comparison.findings_of(session, base.id) if base and base_ok else []
        diff = diff_findings(base_findings, head_findings, {})
        fixed, _ = await comparison.classify_absent(session, diff.absent, head, base, {})
        fixed_ids = {f.id for f in fixed}
        suppressed_paths = {
            str(item["path"]) for item in check.files if "suppression_added" in flags_of(item)
        }
        outcomes: dict[str, str] = {}
        for _, other in diff.unchanged:
            outcomes[other.id] = IssueOutcome.STILL_PRESENT.value
        for finding in diff.absent:
            if finding.path in suppressed_paths:
                outcomes[finding.id] = IssueOutcome.SUPPRESSED.value
            elif finding.id in fixed_ids:
                outcomes[finding.id] = IssueOutcome.FIXED.value
            else:
                outcomes[finding.id] = IssueOutcome.NOT_RECHECKED.value
        incomplete, cache = await comparison.engine_summary(session, head.id)
        new_sorted = sorted(
            diff.new, key=lambda f: (_RANK.get(f.severity, 9), f.path, f.start_line or 0)
        )
        counts = {outcome.value: 0 for outcome in IssueOutcome}
        for outcome in outcomes.values():
            counts[outcome] += 1
        partial = head.state == ScanState.PARTIAL.value or not base_ok
        return {
            "state": (
                ChangeSetCheckState.PARTIAL if partial else ChangeSetCheckState.SUCCEEDED
            ).value,
            "counts": {**counts, "new": len(diff.new)},
            "outcomes": outcomes,
            "new_items": [comparison.item(f) for f in new_sorted[:_NEW_ITEMS]],
            "incomplete": incomplete,
            "base_missing": not base_ok,
            "cache": cache,
            "compiled": False,
            "not_verified": "Source-level checks only: not compiled, built or tested.",
        }

    # -- finalize -------------------------------------------------------------------------------

    @activity.defn(name="cscheck.finalize")
    async def finalize(self, payload: ChangeSetCheckFinalize) -> ChangeSetCheckResult:
        if payload.canceled:
            return await self._end(payload.check_id, ChangeSetCheckState.CANCELED)
        return await self._end(
            payload.check_id,
            ChangeSetCheckState.FAILED,
            code="interrupted",
            message="The check was interrupted; run it again.",
        )

    def all(self) -> list[object]:
        return [self.prepare, self.complete, self.finalize]


@workflow.defn(name=CHANGE_SET_CHECK_WORKFLOW_NAME)
class ChangeSetCheckWorkflow:
    @workflow.run
    async def run(self, payload: ChangeSetCheckInput) -> ChangeSetCheckResult:
        try:
            plan: ChangeSetCheckPlan = await workflow.execute_activity(
                "cscheck.prepare",
                payload,
                result_type=ChangeSetCheckPlan,
                start_to_close_timeout=timedelta(minutes=30),
                heartbeat_timeout=timedelta(minutes=5),
                cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2)),
            )
            if plan.terminal:
                return ChangeSetCheckResult(check_id=payload.check_id, state="TERMINAL")
            for scan_id in plan.scan_ids:
                await workflow.execute_child_workflow(
                    SCAN_WORKFLOW_NAME,
                    ScanWorkflowInput(scan_id=scan_id),
                    id=scan_workflow_id(scan_id),
                    result_type=ScanWorkflowResult,
                )
            result: ChangeSetCheckResult = await workflow.execute_activity(
                "cscheck.complete",
                payload,
                result_type=ChangeSetCheckResult,
                start_to_close_timeout=timedelta(minutes=10),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        except (asyncio.CancelledError, ActivityError, ChildWorkflowError) as exc:
            canceled = is_cancelled_exception(exc)
            final: ChangeSetCheckResult = await workflow.execute_activity(
                "cscheck.finalize",
                ChangeSetCheckFinalize(
                    check_id=payload.check_id, canceled=canceled, interrupted=not canceled
                ),
                result_type=ChangeSetCheckResult,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
            return final
        return result
