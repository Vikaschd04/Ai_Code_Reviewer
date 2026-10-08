"""Worker assembly shared by the ``crp-worker`` process and integration tests."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine
from temporalio.client import Client
from temporalio.worker import Worker

from crp_analysis.engines.base import EngineAdapter
from crp_core.artifacts import ArtifactStore
from crp_core.config import Settings
from crp_core.db.session import create_session_factory
from crp_core.workflows.contracts import git_review_workflow_id
from crp_core.workflows.temporal import TemporalWorkflowGateway
from crp_worker.ai_run import AiRunActivities, AiRunWorkflow
from crp_worker.change_set import ChangeSetCheckActivities, ChangeSetCheckWorkflow
from crp_worker.diagnostics import DiagnosticActivities, DiagnosticWorkflow
from crp_worker.fix_validation import FixActivities, FixValidationWorkflow
from crp_worker.git_review import GitReviewActivities, GitReviewWorkflow, reconcile_due
from crp_worker.intake import IntakeActivities, IntakeWorkflow
from crp_worker.scan import ScanActivities, ScanWorkflow, default_adapters

logger = logging.getLogger(__name__)
RECONCILE_INTERVAL_SECONDS = 3600


def worker_identity() -> str:
    return f"crp-worker@{socket.gethostname()}:{os.getpid()}"


def build_worker(
    client: Client,
    settings: Settings,
    *,
    store: ArtifactStore,
    engine: AsyncEngine,
    identity: str | None = None,
    adapters: dict[str, EngineAdapter] | None = None,
    ai_transport: httpx.AsyncBaseTransport | None = None,
) -> Worker:
    name = identity or worker_identity()
    sessions = create_session_factory(engine)
    activities: list[Any] = [
        *DiagnosticActivities(store, engine, name).all(),
        *IntakeActivities(settings, store, sessions).all(),
        *ScanActivities(settings, store, sessions, adapters).all(),
        *AiRunActivities(settings, store, sessions, transport=ai_transport).all(),
        *FixActivities(
            settings, store, sessions, {**default_adapters(settings), **(adapters or {})}
        ).all(),
    ]

    async def cancel_review(review_id: UUID) -> None:
        await client.get_workflow_handle(git_review_workflow_id(review_id)).cancel()

    activities += GitReviewActivities(settings, store, sessions, cancel_review=cancel_review).all()
    activities += ChangeSetCheckActivities(settings, store, sessions).all()
    return Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[
            DiagnosticWorkflow,
            IntakeWorkflow,
            ScanWorkflow,
            AiRunWorkflow,
            FixValidationWorkflow,
            GitReviewWorkflow,
            ChangeSetCheckWorkflow,
        ],
        activities=activities,
        identity=name,
        max_concurrent_activities=8,
        # Cancellation reaches activities through heartbeat responses; keep that latency short.
        max_heartbeat_throttle_interval=timedelta(seconds=2),
        default_heartbeat_throttle_interval=timedelta(seconds=2),
    )


async def reconcile_loop(settings: Settings, engine: AsyncEngine, stop: asyncio.Event) -> None:
    """Hourly: queue a full review for connected repositories whose last one is too old."""
    gateway = TemporalWorkflowGateway(settings)
    sessions = create_session_factory(engine)
    try:
        while not stop.is_set():
            try:
                queued = await reconcile_due(sessions, gateway.start_git_review)
                if queued:
                    logger.info("queued scheduled full reviews", extra={"reviews": len(queued)})
            except Exception:
                logger.exception("scheduled full reviews could not be queued")
            try:
                await asyncio.wait_for(stop.wait(), timeout=RECONCILE_INTERVAL_SECONDS)
            except TimeoutError:
                continue
    finally:
        await gateway.close()
