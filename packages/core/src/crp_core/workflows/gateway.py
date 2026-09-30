"""Workflow-engine boundary used by the control plane.

The API depends on this protocol rather than on Temporal directly so the engine stays replaceable
and failure states can be simulated in tests. The production implementation is
``crp_core.workflows.temporal.TemporalWorkflowGateway``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from crp_core.workflows.contracts import DiagnosticWorkflowInput, DiagnosticWorkflowResult


class WorkflowUnavailableError(RuntimeError):
    """The workflow service cannot be reached or rejected the request for an operational reason."""


class WorkflowRunNotFoundError(LookupError):
    pass


class WorkflowRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    TERMINATED = "TERMINATED"
    TIMED_OUT = "TIMED_OUT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class WorkflowServiceStatus:
    address: str
    namespace: str
    server_version: str | None


@dataclass(frozen=True, slots=True)
class WorkerPollerStatus:
    task_queue: str
    workflow_pollers: int
    activity_pollers: int
    newest_poll_age_seconds: float | None


@dataclass(frozen=True, slots=True)
class DiagnosticRun:
    workflow_id: str
    status: WorkflowRunStatus
    started_at: datetime | None
    closed_at: datetime | None
    result: DiagnosticWorkflowResult | None
    failure_message: str | None


class WorkflowGateway(Protocol):
    async def describe_service(self) -> WorkflowServiceStatus: ...

    async def describe_workers(self) -> WorkerPollerStatus: ...

    async def start_diagnostic(self, payload: DiagnosticWorkflowInput) -> str: ...

    async def get_diagnostic(self, workflow_id: str) -> DiagnosticRun: ...

    async def start_intake(self, intake_id: UUID) -> str:
        """Start (or join) intake validation; idempotent per intake."""
        ...

    async def start_scan(self, scan_id: UUID) -> str:
        """Start (or join) the scan workflow; idempotent per scan."""
        ...

    async def cancel_scan(self, scan_id: UUID) -> None: ...

    async def start_ai_run(self, run_id: UUID) -> str:
        """Start (or join) the AI run workflow; idempotent per run id."""
        ...

    async def cancel_ai_run(self, run_id: UUID) -> None: ...

    async def close(self) -> None: ...
