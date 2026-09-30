"""Fix validation activities and workflow (P05; ADR 0014).

``validate`` runs the validation ladder (``crp_analysis.fixes.validation``) for one requested
validation: it reads the finding's file from the frozen upload, applies the proposal's edits to a
copy and re-runs the trusted engines on copies of both versions. The upload is never modified and
no project code is executed. The activity is idempotent (no paid calls), so it may be retried;
results are bound to the patch and result hashes recorded when the validation was requested, and
the proposal only changes state if its patch is still the one that was validated.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from crp_analysis.engines.base import CancelToken, EngineAdapter
    from crp_analysis.fixes.patching import Edit
    from crp_analysis.fixes.validation import LadderInput, TargetFinding, run_ladder
    from crp_analysis.manifest import blob_key
    from crp_core.artifacts import ArtifactKey, ArtifactStore
    from crp_core.config import Settings
    from crp_core.db.models import FileEntry, Finding, FixProposal, FixValidation
    from crp_core.db.session import transaction
    from crp_core.domain.states import FixProposalState, FixValidationState
    from crp_core.workflows.contracts import (
        FIX_VALIDATION_WORKFLOW_NAME,
        FixValidationFinalize,
        FixValidationInput,
        FixValidationResult,
    )
    from crp_worker.progress import heartbeat

logger = logging.getLogger(__name__)


def platform_of(path: str, language: str | None, sap: bool) -> str | None:
    if language == "apex" or path.endswith("-meta.xml") or path.endswith("sfdx-project.json"):
        return "salesforce"
    return "sap" if sap else None


class FixActivities:
    def __init__(
        self,
        settings: Settings,
        store: ArtifactStore,
        sessions: async_sessionmaker[AsyncSession],
        adapters: dict[str, EngineAdapter],
    ) -> None:
        self._settings = settings
        self._store = store
        self._sessions = sessions
        self._adapters = adapters

    async def _finish(
        self,
        validation_id: UUID,
        state: FixValidationState,
        *,
        steps: list[dict[str, str]] | None = None,
        summary: str | None = None,
        error: tuple[str, str] | None = None,
    ) -> FixValidationResult:
        async with transaction(self._sessions) as session:
            validation = await session.get(FixValidation, validation_id, with_for_update=True)
            if validation is None:
                return FixValidationResult(validation_id=validation_id, state="MISSING")
            if FixValidationState(validation.state).is_terminal:
                return FixValidationResult(validation_id=validation_id, state=validation.state)
            validation.state = state.value
            validation.finished_at = datetime.now(UTC)
            if steps is not None:
                validation.steps = [dict(step) for step in steps]
            if summary is not None:
                validation.summary = summary
            if error is not None:
                validation.error_code, validation.error_message = error
            proposal = await session.get(FixProposal, validation.proposal_id, with_for_update=True)
            current = proposal is not None and proposal.patch_sha256 == validation.patch_sha256
            if proposal is not None and current and proposal.state == FixProposalState.VALIDATING:
                proposal.state = {
                    FixValidationState.PASSED: FixProposalState.VALIDATED,
                    FixValidationState.FAILED: FixProposalState.VALIDATION_FAILED,
                }.get(state, FixProposalState.PROPOSED).value
            return FixValidationResult(validation_id=validation_id, state=state.value)

    @activity.defn(name="fix.validate")
    async def validate(self, payload: FixValidationInput) -> FixValidationResult:
        async with transaction(self._sessions) as session:
            validation = await session.get(
                FixValidation, payload.validation_id, with_for_update=True
            )
            if validation is None:
                return FixValidationResult(validation_id=payload.validation_id, state="MISSING")
            if FixValidationState(validation.state).is_terminal:
                return FixValidationResult(validation_id=validation.id, state=validation.state)
            if validation.cancel_requested_at is not None:
                cancel_now = True
            else:
                cancel_now = False
                validation.state = FixValidationState.RUNNING.value
                validation.started_at = validation.started_at or datetime.now(UTC)
            missing = await session.get(FixProposal, validation.proposal_id) is None
        if missing:
            return await self._finish(
                payload.validation_id,
                FixValidationState.FAILED,
                error=("proposal_missing", "the fix proposal no longer exists"),
            )
        async with transaction(self._sessions) as session:
            proposal = await session.get(FixProposal, validation.proposal_id)
            if proposal is None:
                return FixValidationResult(validation_id=payload.validation_id, state="MISSING")
            entry = await session.scalar(
                select(FileEntry).where(
                    FileEntry.snapshot_id == proposal.snapshot_id,
                    FileEntry.path == proposal.path,
                )
            )
            finding = await session.get(Finding, proposal.finding_id)
            sap = (
                await session.scalar(
                    select(FileEntry.id)
                    .where(
                        FileEntry.snapshot_id == proposal.snapshot_id,
                        FileEntry.path.like("%extensioninfo.xml"),
                    )
                    .limit(1)
                )
                is not None
            )
            edits = tuple(Edit.from_json(e) for e in proposal.edits)
            expected = (validation.patch_sha256, validation.result_sha256)
            path, allowed = proposal.path, frozenset(proposal.allowed_paths)
            base_sha, snapshot_blob = proposal.base_sha256, entry.blob_sha256 if entry else None
            language = entry.language if entry else None
            target = TargetFinding(
                finding.engine if finding else "",
                finding.rule_id if finding else "",
                proposal.target_line,
            )
        if cancel_now:
            return await self._finish(payload.validation_id, FixValidationState.CANCELED)
        if snapshot_blob is None:
            return await self._finish(
                payload.validation_id,
                FixValidationState.FAILED,
                summary="The file is no longer stored for this upload.",
                error=("file_missing", "the proposal's file is not stored for this upload"),
            )
        data = await asyncio.to_thread(
            self._store.read_bytes,
            ArtifactKey(blob_key(snapshot_blob)),
            max_bytes=self._settings.intake_max_text_file_bytes,
        )
        ladder = LadderInput(
            path=path,
            language=language,
            base_text=data.decode("utf-8", errors="replace"),
            edits=edits,
            base_sha256=base_sha,
            result_sha256=expected[1],
            patch_sha256=expected[0],
            allowed_paths=allowed,
            finding=target,
            platform=platform_of(path, language, sap),
        )
        cancel = CancelToken()
        work = Path(tempfile.mkdtemp(prefix="crp-fix-", dir=self._work_root()))
        job = asyncio.ensure_future(
            asyncio.to_thread(run_ladder, ladder, self._adapters, work, cancel=cancel)
        )
        try:
            while not job.done():
                heartbeat("validating fix")
                if await self._cancel_requested(payload.validation_id):
                    cancel.cancel()
                await asyncio.wait({job}, timeout=1)
        except asyncio.CancelledError:
            cancel.cancel()
            await asyncio.shield(asyncio.wait({job}, timeout=30))
            raise
        result = job.result()
        if result.canceled:
            return await self._finish(
                payload.validation_id,
                FixValidationState.CANCELED,
                steps=result.steps_json(),
                summary=result.summary,
            )
        logger.info(
            "fix validated",
            extra={"validation_id": str(payload.validation_id), "passed": result.passed},
        )
        return await self._finish(
            payload.validation_id,
            FixValidationState.PASSED if result.passed else FixValidationState.FAILED,
            steps=result.steps_json(),
            summary=result.summary,
        )

    def _work_root(self) -> str:
        root = self._settings.work_root
        root.mkdir(parents=True, exist_ok=True)
        return str(root)

    async def _cancel_requested(self, validation_id: UUID) -> bool:
        async with transaction(self._sessions) as session:
            requested = await session.scalar(
                select(FixValidation.cancel_requested_at).where(FixValidation.id == validation_id)
            )
        return requested is not None

    @activity.defn(name="fix.finalize")
    async def finalize(self, payload: FixValidationFinalize) -> FixValidationResult:
        if payload.canceled:
            return await self._finish(payload.validation_id, FixValidationState.CANCELED)
        return await self._finish(
            payload.validation_id,
            FixValidationState.FAILED,
            summary="The validation was interrupted. Run it again.",
            error=("interrupted", "the validation was interrupted"),
        )

    def all(self) -> list[object]:
        return [self.validate, self.finalize]


@workflow.defn(name=FIX_VALIDATION_WORKFLOW_NAME)
class FixValidationWorkflow:
    @workflow.run
    async def run(self, payload: FixValidationInput) -> FixValidationResult:
        try:
            result: FixValidationResult = await workflow.execute_activity(
                "fix.validate",
                payload,
                result_type=FixValidationResult,
                start_to_close_timeout=timedelta(minutes=30),
                heartbeat_timeout=timedelta(minutes=2),
                cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                retry_policy=RetryPolicy(maximum_attempts=2),  # idempotent; no paid calls
            )
        except (asyncio.CancelledError, ActivityError) as exc:
            canceled = is_cancelled_exception(exc)
            final: FixValidationResult = await workflow.execute_activity(
                "fix.finalize",
                FixValidationFinalize(
                    validation_id=payload.validation_id, canceled=canceled, interrupted=not canceled
                ),
                result_type=FixValidationResult,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
            return final
        return result
