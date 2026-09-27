"""Worker assembly shared by the ``crp-worker`` process and integration tests."""

from __future__ import annotations

import os
import socket
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine
from temporalio.client import Client
from temporalio.worker import Worker

from crp_analysis.engines.base import EngineAdapter
from crp_core.artifacts import ArtifactStore
from crp_core.config import Settings
from crp_core.db.session import create_session_factory
from crp_worker.diagnostics import DiagnosticActivities, DiagnosticWorkflow
from crp_worker.intake import IntakeActivities, IntakeWorkflow
from crp_worker.scan import ScanActivities, ScanWorkflow


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
) -> Worker:
    name = identity or worker_identity()
    sessions = create_session_factory(engine)
    activities: list[Any] = [
        *DiagnosticActivities(store, engine, name).all(),
        *IntakeActivities(settings, store, sessions).all(),
        *ScanActivities(settings, store, sessions, adapters).all(),
    ]
    return Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[DiagnosticWorkflow, IntakeWorkflow, ScanWorkflow],
        activities=activities,
        identity=name,
        max_concurrent_activities=8,
        # Cancellation reaches activities through heartbeat responses; keep that latency short.
        max_heartbeat_throttle_interval=timedelta(seconds=2),
        default_heartbeat_throttle_interval=timedelta(seconds=2),
    )
