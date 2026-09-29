"""In-process workflow runner for lite deployments (``CRP_PROFILE=lite``; ADR 0010).

Implements the ``WorkflowGateway`` contract without a Temporal server by running the same
activities (intake, scan, diagnostic) as asyncio tasks inside the API process. Behaviour kept:
workflow IDs are idempotent (one run per ID at a time), activities are retried like the
Temporal retry policies, scan cancellation stops the running engine and finalizes CANCELED,
and every outcome is published through the same transactional activity code.

Not kept: durability across process restarts. On start, unfinished intakes (VALIDATING) and
scans (QUEUED/RUNNING) are resumed from database state, which is safe because every activity is
idempotent. Engines run one at a time to bound memory.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from crp_analysis.engines.base import EngineAdapter
from crp_core.artifacts import ArtifactStore
from crp_core.config import Settings
from crp_core.db.models import Intake, Scan
from crp_core.db.session import create_session_factory, transaction
from crp_core.domain.states import IntakeState, ScanState
from crp_core.workflows.contracts import (
    DIAGNOSTIC_WORKFLOW_ID_PATTERN,
    DiagnosticWorkflowInput,
    DiagnosticWorkflowResult,
    EngineTask,
    FinalizeInput,
    IntakeWorkflowInput,
    ScanWorkflowInput,
    diagnostic_workflow_id,
    intake_workflow_id,
    scan_workflow_id,
)
from crp_core.workflows.gateway import (
    DiagnosticRun,
    WorkerPollerStatus,
    WorkflowRunNotFoundError,
    WorkflowRunStatus,
    WorkflowServiceStatus,
)
from crp_worker.diagnostics import DiagnosticActivities
from crp_worker.intake import IntakeActivities
from crp_worker.scan import ScanActivities

logger = logging.getLogger(__name__)
INLINE_QUEUE = "in-process"


async def _retry[T](call: Callable[[], Awaitable[T]], *, attempts: int, delay: float = 2.0) -> T:
    """Retry like a Temporal RetryPolicy (cancellation is never retried)."""
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except asyncio.CancelledError:
            raise
        except Exception:
            if attempt == attempts:
                raise
            logger.warning("activity attempt %d/%d failed; retrying", attempt, attempts)
            await asyncio.sleep(delay * attempt)
    raise AssertionError("unreachable")


@dataclass(slots=True)
class _Diagnostic:
    started_at: datetime
    closed_at: datetime | None = None
    result: DiagnosticWorkflowResult | None = None
    failure: str | None = None


class InlineWorkflowGateway:
    def __init__(
        self,
        settings: Settings,
        store: ArtifactStore,
        engine: AsyncEngine,
        *,
        adapters: dict[str, EngineAdapter] | None = None,
        max_concurrent_scans: int = 1,
    ) -> None:
        sessions = create_session_factory(engine)
        self._sessions = sessions
        self._identity = f"crp-inline@{socket.gethostname()}:{os.getpid()}"
        self._intake = IntakeActivities(settings, store, sessions)
        self._scan = ScanActivities(settings, store, sessions, adapters)
        self._diagnostics = DiagnosticActivities(store, engine, self._identity)
        self._scan_slots = asyncio.Semaphore(max_concurrent_scans)
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._diagnostic_runs: dict[str, _Diagnostic] = {}
        self._last_activity = datetime.now(UTC)

    # -- lifecycle --------------------------------------------------------------------------

    async def start(self) -> None:
        """Resume work that a previous process left unfinished (activities are idempotent)."""
        async with transaction(self._sessions) as session:
            intakes = (
                await session.execute(
                    select(Intake.id).where(Intake.state == IntakeState.VALIDATING.value)
                )
            ).scalars()
            scans = (
                await session.execute(
                    select(Scan.id)
                    .where(Scan.state.in_([ScanState.QUEUED.value, ScanState.RUNNING.value]))
                    .order_by(Scan.created_at)
                )
            ).scalars()
            pending_intakes, pending_scans = list(intakes), list(scans)
        for intake_id in pending_intakes:
            await self.start_intake(intake_id)
        for scan_id in pending_scans:
            await self.start_scan(scan_id)
        if pending_intakes or pending_scans:
            logger.info(
                "resumed unfinished work",
                extra={"intakes": len(pending_intakes), "scans": len(pending_scans)},
            )

    async def close(self) -> None:
        tasks = [t for t in self._tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=30)

    def _spawn(self, workflow_id: str, work: Callable[[], Awaitable[Any]]) -> None:
        running = self._tasks.get(workflow_id)
        if running is not None and not running.done():
            return  # same semantics as Temporal's USE_EXISTING conflict policy
        self._tasks[workflow_id] = asyncio.create_task(self._guard(workflow_id, work))

    async def _guard(self, workflow_id: str, work: Callable[[], Awaitable[Any]]) -> None:
        try:
            await work()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("in-process workflow failed", extra={"workflow_id": workflow_id})
        finally:
            self._last_activity = datetime.now(UTC)

    # -- status (readiness) ---------------------------------------------------------------------

    async def describe_service(self) -> WorkflowServiceStatus:
        return WorkflowServiceStatus(
            address=INLINE_QUEUE, namespace=INLINE_QUEUE, server_version=None
        )

    async def describe_workers(self) -> WorkerPollerStatus:
        return WorkerPollerStatus(
            task_queue=INLINE_QUEUE,
            workflow_pollers=1,
            activity_pollers=1,
            newest_poll_age_seconds=0.0,
        )

    # -- intake -----------------------------------------------------------------------------

    async def start_intake(self, intake_id: UUID) -> str:
        workflow_id = intake_workflow_id(intake_id)
        self._spawn(workflow_id, lambda: self._run_intake(intake_id))
        return workflow_id

    async def _run_intake(self, intake_id: UUID) -> None:
        payload = IntakeWorkflowInput(intake_id=intake_id)
        try:
            await _retry(lambda: self._intake.process(payload), attempts=3)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("intake processing failed", extra={"intake_id": str(intake_id)})
            await _retry(lambda: self._intake.mark_failed(payload), attempts=5)

    # -- scan -------------------------------------------------------------------------------

    async def start_scan(self, scan_id: UUID) -> str:
        workflow_id = scan_workflow_id(scan_id)
        self._spawn(workflow_id, lambda: self._run_scan(scan_id))
        return workflow_id

    async def cancel_scan(self, scan_id: UUID) -> None:
        task = self._tasks.get(scan_workflow_id(scan_id))
        if task is not None and not task.done():
            task.cancel()
        else:
            # Queued behind another scan or not started in this process: prepare() sees the
            # recorded cancel request and ends the scan CANCELED.
            await self.start_scan(scan_id)

    async def _run_scan(self, scan_id: UUID) -> None:
        payload = ScanWorkflowInput(scan_id=scan_id)
        prepared = False
        try:
            async with self._scan_slots:
                plan = await _retry(lambda: self._scan.prepare(payload), attempts=5)
                prepared = True
                if plan.terminal:
                    return
                for engine in plan.engines:
                    task = EngineTask(scan_id=scan_id, engine=engine)
                    try:
                        await _retry(partial(self._scan.run_engine, task), attempts=2)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("engine activity failed", extra={"engine": engine})
                await _retry(
                    lambda: self._scan.finalize(FinalizeInput(scan_id=scan_id)), attempts=5
                )
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None:
                current.uncancel()
            if prepared:
                await _retry(
                    lambda: self._scan.finalize(FinalizeInput(scan_id=scan_id, canceled=True)),
                    attempts=5,
                )
            else:
                await _retry(lambda: self._scan.prepare(payload), attempts=5)

    # -- diagnostic -------------------------------------------------------------------------

    async def start_diagnostic(self, payload: DiagnosticWorkflowInput) -> str:
        workflow_id = diagnostic_workflow_id(payload.run_id)
        record = _Diagnostic(started_at=datetime.now(UTC))
        self._diagnostic_runs[workflow_id] = record

        async def run() -> None:
            try:
                identity = await self._diagnostics.worker_identity()
                artifact = await self._diagnostics.artifact_roundtrip(payload)
                database = await self._diagnostics.database_probe()
                record.result = DiagnosticWorkflowResult(
                    run_id=payload.run_id,
                    worker_identity=identity,
                    artifact=artifact,
                    database=database,
                    started_at=record.started_at,
                    completed_at=datetime.now(UTC),
                )
            except Exception as exc:
                logger.exception("diagnostic failed")
                record.failure = f"{type(exc).__name__}: diagnostic failed"
            finally:
                record.closed_at = datetime.now(UTC)

        self._spawn(workflow_id, run)
        return workflow_id

    async def get_diagnostic(self, workflow_id: str) -> DiagnosticRun:
        if not DIAGNOSTIC_WORKFLOW_ID_PATTERN.fullmatch(workflow_id):
            raise WorkflowRunNotFoundError(workflow_id)
        record = self._diagnostic_runs.get(workflow_id)
        if record is None:
            raise WorkflowRunNotFoundError(workflow_id)
        if record.result is not None:
            status = WorkflowRunStatus.COMPLETED
        elif record.failure is not None:
            status = WorkflowRunStatus.FAILED
        else:
            status = WorkflowRunStatus.RUNNING
        return DiagnosticRun(
            workflow_id=workflow_id,
            status=status,
            started_at=record.started_at,
            closed_at=record.closed_at,
            result=record.result,
            failure_message=record.failure,
        )
