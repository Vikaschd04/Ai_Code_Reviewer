"""Intake validation workflow: validate the uploaded archive, freeze the snapshot, persist manifest.

The raw archive and client manifest are transient: they are deleted once validation reaches a
terminal state (READY or REJECTED). Rejections are final (non-retryable); infrastructure errors
are retried by Temporal and end in FAILED only after retries are exhausted.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from crp_analysis.client_manifest import ClientManifest
    from crp_analysis.inventory import build_inventory
    from crp_analysis.manifest import manifest_document
    from crp_analysis.policy import POLICY_VERSION
    from crp_analysis.zip_intake import (
        IntakeLimits,
        IntakeOutcome,
        IntakeRejectedError,
        process_zip,
    )
    from crp_core.artifacts import ArtifactKey, ArtifactStore
    from crp_core.config import Settings
    from crp_core.db.models import FileEntry, Intake, Snapshot
    from crp_core.db.session import transaction
    from crp_core.domain.states import CaptureStatus, IntakeState
    from crp_core.workflows.contracts import (
        INTAKE_WORKFLOW_NAME,
        IntakeWorkflowInput,
        IntakeWorkflowResult,
    )
    from crp_worker.progress import heartbeat

logger = logging.getLogger(__name__)


def limits_from(settings: Settings) -> IntakeLimits:
    return IntakeLimits(
        max_archive_bytes=settings.intake_max_upload_bytes,
        max_expanded_bytes=settings.intake_max_expanded_bytes,
        max_entries=settings.intake_max_entries,
        max_text_file_bytes=settings.intake_max_text_file_bytes,
        max_compression_ratio=settings.intake_max_compression_ratio,
        max_path_length=settings.intake_max_path_length,
        max_path_depth=settings.intake_max_path_depth,
    )


async def freeze_snapshot(
    session: AsyncSession,
    store: ArtifactStore,
    intake: Intake,
    outcome: IntakeOutcome,
    inventory: dict[str, object],
    *,
    git: dict[str, object] | None = None,
) -> UUID:
    """Create the intake's frozen snapshot and file entries (idempotent) and mark it READY.

    ``git`` carries commit metadata for provider captures (P06): ``commit``, ``ref``,
    ``provider``, ``repository``, ``tree`` and the capture report.
    """
    existing = (
        await session.execute(select(Snapshot.id).where(Snapshot.intake_id == intake.id))
    ).scalar_one_or_none()
    if existing is None:
        snapshot = Snapshot(
            workspace_id=intake.workspace_id,
            project_id=intake.project_id,
            source_id=intake.source_id,
            capture_status=CaptureStatus.FROZEN.value,
            manifest_sha256=outcome.digest,
            file_count=len(outcome.entries),
            total_bytes=sum(e.size_bytes or 0 for e in outcome.entries),
            frozen_at=datetime.now(UTC),
            intake_id=intake.id,
            policy_version=POLICY_VERSION,
            analyzable_count=outcome.analyzable_count,
            excluded_count=outcome.excluded_count,
            inventory=inventory,
        )
        if git is not None:
            snapshot.git_commit = str(git["commit"])
            snapshot.git_ref = str(git["ref"]) if git.get("ref") else None
            snapshot.git_provider = str(git["provider"])
            snapshot.git_repository = str(git["repository"])
            snapshot.git_tree_sha = str(git["tree"])
            report = git.get("capture")
            snapshot.git_capture = report if isinstance(report, dict) else None
        session.add(snapshot)
        await session.flush()
        key = ArtifactKey(f"snapshots/{snapshot.id.hex}/manifest.json")
        document = manifest_document(outcome.entries, policy_version=POLICY_VERSION)
        await asyncio.to_thread(store.put_bytes, key, document, overwrite=True)
        snapshot.manifest_key = str(key)
        session.add_all(
            FileEntry(
                workspace_id=intake.workspace_id,
                project_id=intake.project_id,
                snapshot_id=snapshot.id,
                path=entry.path,
                disposition=entry.disposition.value,
                reason=entry.reason,
                size_bytes=entry.size_bytes,
                blob_sha256=entry.sha256 if entry.disposition.value == "ANALYZABLE" else None,
                language=entry.language,
                category=entry.category,
                line_count=entry.line_count,
            )
            for entry in outcome.entries
        )
        existing = snapshot.id
    intake.state = IntakeState.READY.value
    intake.snapshot_id = existing
    intake.finalized_at = datetime.now(UTC)
    return existing


class IntakeActivities:
    def __init__(
        self,
        settings: Settings,
        store: ArtifactStore,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._settings = settings
        self._store = store
        self._sessions = session_factory

    def _validate(self, intake: Intake) -> IntakeOutcome:
        client: ClientManifest | None = None
        if intake.client_manifest_key:
            raw = self._store.read_bytes(
                ArtifactKey(intake.client_manifest_key),
                max_bytes=self._settings.intake_client_manifest_max_bytes,
            )
            client = ClientManifest.model_validate_json(raw)
        if intake.archive_key is None or intake.archive_bytes is None:
            raise IntakeRejectedError("no_content", "No archive was uploaded for this intake.")
        with self._store.open_read(ArtifactKey(intake.archive_key)) as archive:
            return process_zip(
                archive,
                archive_bytes=intake.archive_bytes,
                store=self._store,
                limits=limits_from(self._settings),
                client_manifest=client,
            )

    def _discard_upload(self, intake: Intake) -> None:
        for key in (intake.archive_key, intake.client_manifest_key):
            if key:
                self._store.delete(ArtifactKey(key))

    @activity.defn(name="intake.process")
    async def process(self, payload: IntakeWorkflowInput) -> IntakeWorkflowResult:
        async with transaction(self._sessions) as session:
            intake = await session.get(Intake, payload.intake_id)
            if intake is None:
                raise RuntimeError("intake not found")
            if IntakeState(intake.state) is not IntakeState.VALIDATING:
                return IntakeWorkflowResult(
                    intake_id=intake.id, state=intake.state, snapshot_id=intake.snapshot_id
                )
            session.expunge(intake)

        heartbeat("validating archive")
        try:
            outcome = await asyncio.to_thread(self._validate, intake)
        except IntakeRejectedError as exc:
            return await self._finish_rejected(intake, exc)
        inventory = build_inventory(outcome.entries, outcome.inventory_texts)
        heartbeat("persisting manifest")
        return await self._finish_ready(intake, outcome, inventory)

    async def _finish_rejected(
        self, intake: Intake, exc: IntakeRejectedError
    ) -> IntakeWorkflowResult:
        async with transaction(self._sessions) as session:
            row = await session.get(Intake, intake.id, with_for_update=True)
            if row is not None and IntakeState(row.state) is IntakeState.VALIDATING:
                row.state = IntakeState.REJECTED.value
                row.error_code = exc.code
                row.error_message = exc.message
                row.error_details = exc.details
                row.finalized_at = datetime.now(UTC)
        await asyncio.to_thread(self._discard_upload, intake)
        logger.info("intake rejected", extra={"intake_id": str(intake.id), "code": exc.code})
        return IntakeWorkflowResult(intake_id=intake.id, state="REJECTED", error_code=exc.code)

    async def _finish_ready(
        self, intake: Intake, outcome: IntakeOutcome, inventory: dict[str, object]
    ) -> IntakeWorkflowResult:
        snapshot_id = None
        async with transaction(self._sessions) as session:
            row = await session.get(Intake, intake.id, with_for_update=True)
            if row is None or IntakeState(row.state) is not IntakeState.VALIDATING:
                state = row.state if row is not None else "MISSING"
                return IntakeWorkflowResult(intake_id=intake.id, state=state)
            snapshot_id = await freeze_snapshot(session, self._store, row, outcome, inventory)
        await asyncio.to_thread(self._discard_upload, intake)
        return IntakeWorkflowResult(intake_id=intake.id, state="READY", snapshot_id=snapshot_id)

    @activity.defn(name="intake.mark_failed")
    async def mark_failed(self, payload: IntakeWorkflowInput) -> None:
        async with transaction(self._sessions) as session:
            row = await session.get(Intake, payload.intake_id, with_for_update=True)
            if row is not None and IntakeState(row.state) is IntakeState.VALIDATING:
                row.state = IntakeState.FAILED.value
                row.error_code = "validation_failed"
                row.error_message = "Intake validation failed after retries; see worker logs."
                row.finalized_at = datetime.now(UTC)

    def all(self) -> list[object]:
        return [self.process, self.mark_failed]


@workflow.defn(name=INTAKE_WORKFLOW_NAME)
class IntakeWorkflow:
    @workflow.run
    async def run(self, payload: IntakeWorkflowInput) -> IntakeWorkflowResult:
        try:
            result: IntakeWorkflowResult = await workflow.execute_activity(
                "intake.process",
                payload,
                result_type=IntakeWorkflowResult,
                start_to_close_timeout=timedelta(minutes=30),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2)),
            )
        except ActivityError:
            await workflow.execute_activity(
                "intake.mark_failed",
                payload,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            return IntakeWorkflowResult(
                intake_id=payload.intake_id, state="FAILED", error_code="validation_failed"
            )
        return result
