"""Readiness checks of real dependencies: PostgreSQL + schema, Temporal, worker, storage."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from crp_api.container import AppContainer
from crp_api.schemas import CheckStatus, DependencyCheck, OverallReadiness, ReadinessReport
from crp_core.artifacts import ArtifactError
from crp_core.db.migrate import current_revision, head_revision
from crp_core.workflows.gateway import WorkflowUnavailableError

CheckFn = Callable[[], Awaitable[DependencyCheck]]


async def _timed(name: str, check: CheckFn, limit_seconds: float) -> DependencyCheck:
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(check(), timeout=limit_seconds)
    except TimeoutError:
        result = DependencyCheck(
            name=name,
            status=CheckStatus.UNAVAILABLE,
            latency_ms=None,
            summary=f"Check timed out after {limit_seconds:g}s",
            error_code="check_timeout",
        )
    latency = round((time.perf_counter() - started) * 1000, 1)
    return result.model_copy(update={"latency_ms": latency})


async def check_database(container: AppContainer) -> DependencyCheck:
    expected = head_revision()
    try:
        async with container.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            revision = await connection.run_sync(current_revision)
    except (SQLAlchemyError, OSError) as exc:
        return DependencyCheck(
            name="database",
            status=CheckStatus.UNAVAILABLE,
            latency_ms=None,
            summary=f"PostgreSQL is unreachable ({type(exc).__name__})",
            error_code="database_unreachable",
            details={"expected_revision": expected},
        )
    details: dict[str, str | int | float | bool | None] = {
        "schema_revision": revision,
        "expected_revision": expected,
    }
    if revision != expected:
        return DependencyCheck(
            name="database",
            status=CheckStatus.FAILED,
            latency_ms=None,
            summary="Database schema is not at the expected migration; run the migrate command",
            error_code="schema_out_of_date",
            details=details,
        )
    return DependencyCheck(
        name="database",
        status=CheckStatus.OK,
        latency_ms=None,
        summary="PostgreSQL reachable; schema at head",
        details=details,
    )


async def check_workflow_service(container: AppContainer) -> DependencyCheck:
    try:
        service = await container.workflows.describe_service()
    except WorkflowUnavailableError as exc:
        return DependencyCheck(
            name="workflow_service",
            status=CheckStatus.UNAVAILABLE,
            latency_ms=None,
            summary=str(exc),
            error_code="workflow_service_unreachable",
        )
    return DependencyCheck(
        name="workflow_service",
        status=CheckStatus.OK,
        latency_ms=None,
        summary=f"Temporal namespace '{service.namespace}' reachable",
        details={"address": service.address, "server_version": service.server_version},
    )


async def check_workflow_worker(container: AppContainer) -> DependencyCheck:
    try:
        workers = await container.workflows.describe_workers()
    except WorkflowUnavailableError as exc:
        return DependencyCheck(
            name="workflow_worker",
            status=CheckStatus.UNAVAILABLE,
            latency_ms=None,
            summary=f"Cannot query workers: {exc}",
            error_code="workflow_service_unreachable",
        )
    details: dict[str, str | int | float | bool | None] = {
        "task_queue": workers.task_queue,
        "workflow_pollers": workers.workflow_pollers,
        "activity_pollers": workers.activity_pollers,
        "newest_poll_age_seconds": None
        if workers.newest_poll_age_seconds is None
        else round(workers.newest_poll_age_seconds, 1),
    }
    if workers.workflow_pollers == 0 or workers.activity_pollers == 0:
        return DependencyCheck(
            name="workflow_worker",
            status=CheckStatus.UNAVAILABLE,
            latency_ms=None,
            summary=(
                f"No worker has recently polled task queue '{workers.task_queue}'; start the worker"
            ),
            error_code="no_active_worker",
            details=details,
        )
    return DependencyCheck(
        name="workflow_worker",
        status=CheckStatus.OK,
        latency_ms=None,
        summary=f"Worker polling task queue '{workers.task_queue}'",
        details=details,
    )


async def check_artifact_store(container: AppContainer) -> DependencyCheck:
    try:
        backend = await asyncio.to_thread(container.artifacts.probe)
    except (ArtifactError, OSError) as exc:
        return DependencyCheck(
            name="artifact_store",
            status=CheckStatus.FAILED,
            latency_ms=None,
            summary=f"Artifact store write/read probe failed ({type(exc).__name__})",
            error_code="artifact_store_failed",
        )
    return DependencyCheck(
        name="artifact_store",
        status=CheckStatus.OK,
        latency_ms=None,
        summary="Write/read/delete probe succeeded",
        details={"backend": backend},
    )


async def build_readiness_report(container: AppContainer) -> ReadinessReport:
    timeout = container.settings.readiness_check_timeout_seconds
    checks: list[tuple[str, CheckFn]] = [
        ("database", lambda: check_database(container)),
        ("workflow_service", lambda: check_workflow_service(container)),
        ("workflow_worker", lambda: check_workflow_worker(container)),
        ("artifact_store", lambda: check_artifact_store(container)),
    ]
    results = await asyncio.gather(*(_timed(name, fn, timeout) for name, fn in checks))
    overall = (
        OverallReadiness.READY
        if all(result.status is CheckStatus.OK for result in results)
        else OverallReadiness.NOT_READY
    )
    return ReadinessReport(
        status=overall,
        checked_at=datetime.now(UTC),
        environment=container.settings.environment.value,
        checks=list(results),
    )
