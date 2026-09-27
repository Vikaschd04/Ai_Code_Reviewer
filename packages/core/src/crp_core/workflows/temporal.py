"""Temporal implementation of the workflow gateway."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import (
    DescribeNamespaceRequest,
    DescribeTaskQueueRequest,
    GetSystemInfoRequest,
)
from temporalio.client import (
    Client,
    WorkflowExecutionStatus,
    WorkflowFailureError,
)
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.service import RPCError, RPCStatusCode

from crp_core.config import Settings
from crp_core.workflows.contracts import (
    DIAGNOSTIC_WORKFLOW_ID_PATTERN,
    DIAGNOSTIC_WORKFLOW_NAME,
    INTAKE_WORKFLOW_NAME,
    SCAN_WORKFLOW_NAME,
    DiagnosticWorkflowInput,
    DiagnosticWorkflowResult,
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
    WorkflowUnavailableError,
)

logger = logging.getLogger(__name__)

DIAGNOSTIC_EXECUTION_TIMEOUT = timedelta(minutes=2)
INTAKE_EXECUTION_TIMEOUT = timedelta(hours=1)
SCAN_EXECUTION_TIMEOUT = timedelta(hours=8)
_RPC_TIMEOUT = timedelta(seconds=5)

_STATUS_MAP = {
    WorkflowExecutionStatus.RUNNING: WorkflowRunStatus.RUNNING,
    WorkflowExecutionStatus.COMPLETED: WorkflowRunStatus.COMPLETED,
    WorkflowExecutionStatus.FAILED: WorkflowRunStatus.FAILED,
    WorkflowExecutionStatus.CANCELED: WorkflowRunStatus.CANCELED,
    WorkflowExecutionStatus.TERMINATED: WorkflowRunStatus.TERMINATED,
    WorkflowExecutionStatus.TIMED_OUT: WorkflowRunStatus.TIMED_OUT,
}


async def connect_temporal(settings: Settings) -> Client:
    """Connect a Temporal client that serializes Pydantic contracts, bounded by a timeout."""
    try:
        return await asyncio.wait_for(
            Client.connect(
                settings.temporal_address,
                namespace=settings.temporal_namespace,
                data_converter=pydantic_data_converter,
            ),
            timeout=settings.temporal_connect_timeout_seconds,
        )
    except (TimeoutError, RuntimeError, RPCError) as exc:
        raise WorkflowUnavailableError(
            f"Temporal service at {settings.temporal_address} is unreachable: {type(exc).__name__}"
        ) from exc


class TemporalWorkflowGateway:
    """Lazily connected gateway; reconnects on the next call after a connection failure."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Client | None = None
        self._lock = asyncio.Lock()

    async def _get_client(self) -> Client:
        async with self._lock:
            if self._client is None:
                self._client = await connect_temporal(self._settings)
            return self._client

    def _reset_on_unavailable(self, exc: RPCError) -> None:
        if exc.status in {RPCStatusCode.UNAVAILABLE, RPCStatusCode.DEADLINE_EXCEEDED}:
            self._client = None

    async def describe_service(self) -> WorkflowServiceStatus:
        client = await self._get_client()
        service = client.workflow_service
        try:
            info = await service.get_system_info(GetSystemInfoRequest(), timeout=_RPC_TIMEOUT)
            await service.describe_namespace(
                DescribeNamespaceRequest(namespace=self._settings.temporal_namespace),
                timeout=_RPC_TIMEOUT,
            )
        except RPCError as exc:
            self._reset_on_unavailable(exc)
            raise WorkflowUnavailableError(
                f"Temporal namespace check failed: {exc.status.name}"
            ) from exc
        return WorkflowServiceStatus(
            address=self._settings.temporal_address,
            namespace=self._settings.temporal_namespace,
            server_version=info.server_version or None,
        )

    async def describe_workers(self) -> WorkerPollerStatus:
        client = await self._get_client()
        now = datetime.now(UTC)
        counts: dict[int, int] = {}
        newest: datetime | None = None
        fresh_after = now - timedelta(seconds=self._settings.worker_poller_fresh_seconds)
        for queue_type in (
            TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
            TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,
        ):
            try:
                response = await client.workflow_service.describe_task_queue(
                    DescribeTaskQueueRequest(
                        namespace=self._settings.temporal_namespace,
                        task_queue=TaskQueue(name=self._settings.temporal_task_queue),
                        task_queue_type=queue_type,
                    ),
                    timeout=_RPC_TIMEOUT,
                )
            except RPCError as exc:
                self._reset_on_unavailable(exc)
                raise WorkflowUnavailableError(
                    f"Temporal task-queue check failed: {exc.status.name}"
                ) from exc
            fresh = 0
            for poller in response.pollers:
                seen = poller.last_access_time.ToDatetime(tzinfo=UTC)
                if seen >= fresh_after:
                    fresh += 1
                    newest = seen if newest is None or seen > newest else newest
            counts[queue_type] = fresh
        return WorkerPollerStatus(
            task_queue=self._settings.temporal_task_queue,
            workflow_pollers=counts[TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW],
            activity_pollers=counts[TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY],
            newest_poll_age_seconds=None
            if newest is None
            else max(0.0, (now - newest).total_seconds()),
        )

    async def start_diagnostic(self, payload: DiagnosticWorkflowInput) -> str:
        client = await self._get_client()
        workflow_id = diagnostic_workflow_id(payload.run_id)
        try:
            await client.start_workflow(
                DIAGNOSTIC_WORKFLOW_NAME,
                payload,
                id=workflow_id,
                task_queue=self._settings.temporal_task_queue,
                execution_timeout=DIAGNOSTIC_EXECUTION_TIMEOUT,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                rpc_timeout=_RPC_TIMEOUT,
            )
        except RPCError as exc:
            self._reset_on_unavailable(exc)
            raise WorkflowUnavailableError(
                f"could not start diagnostic workflow: {exc.status.name}"
            ) from exc
        return workflow_id

    async def get_diagnostic(self, workflow_id: str) -> DiagnosticRun:
        if not DIAGNOSTIC_WORKFLOW_ID_PATTERN.fullmatch(workflow_id):
            raise WorkflowRunNotFoundError(workflow_id)
        client = await self._get_client()
        handle = client.get_workflow_handle(workflow_id, result_type=DiagnosticWorkflowResult)
        try:
            description = await handle.describe(rpc_timeout=_RPC_TIMEOUT)
        except RPCError as exc:
            if exc.status is RPCStatusCode.NOT_FOUND:
                raise WorkflowRunNotFoundError(workflow_id) from exc
            self._reset_on_unavailable(exc)
            raise WorkflowUnavailableError(
                f"could not describe diagnostic workflow: {exc.status.name}"
            ) from exc

        status = _STATUS_MAP.get(
            description.status or WorkflowExecutionStatus.RUNNING, WorkflowRunStatus.UNKNOWN
        )
        result: DiagnosticWorkflowResult | None = None
        failure: str | None = None
        if status is WorkflowRunStatus.COMPLETED:
            result = await handle.result(rpc_timeout=_RPC_TIMEOUT)
        elif status is WorkflowRunStatus.FAILED:
            try:
                await handle.result(rpc_timeout=_RPC_TIMEOUT)
            except WorkflowFailureError as exc:
                failure = str(exc.cause) if exc.cause else "workflow failed"
        return DiagnosticRun(
            workflow_id=workflow_id,
            status=status,
            started_at=description.start_time,
            closed_at=description.close_time,
            result=result,
            failure_message=failure,
        )

    async def _start_once(
        self, name: str, payload: object, workflow_id: str, execution_timeout: timedelta
    ) -> str:
        client = await self._get_client()
        try:
            await client.start_workflow(
                name,
                payload,
                id=workflow_id,
                task_queue=self._settings.temporal_task_queue,
                execution_timeout=execution_timeout,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                rpc_timeout=_RPC_TIMEOUT,
            )
        except RPCError as exc:
            if exc.status is RPCStatusCode.ALREADY_EXISTS:
                return workflow_id  # already ran to completion: finalize/start is idempotent
            self._reset_on_unavailable(exc)
            raise WorkflowUnavailableError(f"could not start {name}: {exc.status.name}") from exc
        return workflow_id

    async def start_intake(self, intake_id: UUID) -> str:
        return await self._start_once(
            INTAKE_WORKFLOW_NAME,
            IntakeWorkflowInput(intake_id=intake_id),
            intake_workflow_id(intake_id),
            INTAKE_EXECUTION_TIMEOUT,
        )

    async def start_scan(self, scan_id: UUID) -> str:
        return await self._start_once(
            SCAN_WORKFLOW_NAME,
            ScanWorkflowInput(scan_id=scan_id),
            scan_workflow_id(scan_id),
            SCAN_EXECUTION_TIMEOUT,
        )

    async def cancel_scan(self, scan_id: UUID) -> None:
        client = await self._get_client()
        try:
            await client.get_workflow_handle(scan_workflow_id(scan_id)).cancel(
                rpc_timeout=_RPC_TIMEOUT
            )
        except RPCError as exc:
            if exc.status is RPCStatusCode.NOT_FOUND:
                return
            self._reset_on_unavailable(exc)
            raise WorkflowUnavailableError(f"could not cancel scan: {exc.status.name}") from exc

    async def close(self) -> None:
        # temporalio clients hold no explicit close handle; dropping the reference releases it.
        self._client = None
