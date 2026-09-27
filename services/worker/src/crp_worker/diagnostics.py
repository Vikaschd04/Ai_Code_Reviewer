"""Diagnostic workflow proving that the durable execution path works end to end.

It writes and verifies a random probe artifact, checks the database schema revision and records
which worker executed it. It performs no source analysis and produces no findings.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncEngine

    from crp_core.artifacts import ArtifactKey, ArtifactStore
    from crp_core.db.migrate import current_revision, head_revision
    from crp_core.workflows.contracts import (
        DIAGNOSTIC_WORKFLOW_NAME,
        ArtifactProbeResult,
        DatabaseProbeResult,
        DiagnosticWorkflowInput,
        DiagnosticWorkflowResult,
    )

PROBE_BYTES = 4096
_ACTIVITY_TIMEOUT = timedelta(seconds=30)
_RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=1))


class DiagnosticActivities:
    def __init__(self, store: ArtifactStore, engine: AsyncEngine, identity: str) -> None:
        self._store = store
        self._engine = engine
        self._identity = identity

    @activity.defn(name="diagnostic.artifact_roundtrip")
    async def artifact_roundtrip(self, payload: DiagnosticWorkflowInput) -> ArtifactProbeResult:
        key = ArtifactKey(f"diagnostics/{payload.run_id.hex}/probe.bin")
        data = os.urandom(PROBE_BYTES)

        def roundtrip() -> ArtifactProbeResult:
            self._store.delete(key)  # idempotent retry: remove a partial earlier attempt
            ref = self._store.put_bytes(key, data)
            read_back = self._store.read_bytes(key)
            verified = read_back == data and hashlib.sha256(read_back).hexdigest() == ref.sha256
            deleted = self._store.delete(key)
            return ArtifactProbeResult(
                key=str(key),
                sha256=ref.sha256,
                size_bytes=ref.size_bytes,
                read_back_verified=verified,
                deleted=deleted,
            )

        return await asyncio.to_thread(roundtrip)

    @activity.defn(name="diagnostic.database_probe")
    async def database_probe(self) -> DatabaseProbeResult:
        expected = head_revision()
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            revision = await connection.run_sync(current_revision)
        return DatabaseProbeResult(
            reachable=True,
            schema_revision=revision,
            expected_revision=expected,
            schema_current=revision == expected,
        )

    @activity.defn(name="diagnostic.worker_identity")
    async def worker_identity(self) -> str:
        return self._identity

    def all(self) -> list[object]:
        return [self.artifact_roundtrip, self.database_probe, self.worker_identity]


@workflow.defn(name=DIAGNOSTIC_WORKFLOW_NAME)
class DiagnosticWorkflow:
    @workflow.run
    async def run(self, payload: DiagnosticWorkflowInput) -> DiagnosticWorkflowResult:
        started_at = workflow.now()
        identity = await workflow.execute_activity(
            "diagnostic.worker_identity",
            result_type=str,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY,
        )
        artifact = await workflow.execute_activity(
            "diagnostic.artifact_roundtrip",
            payload,
            result_type=ArtifactProbeResult,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY,
        )
        database = await workflow.execute_activity(
            "diagnostic.database_probe",
            result_type=DatabaseProbeResult,
            start_to_close_timeout=_ACTIVITY_TIMEOUT,
            retry_policy=_RETRY,
        )
        return DiagnosticWorkflowResult(
            run_id=payload.run_id,
            worker_identity=identity,
            artifact=artifact,
            database=database,
            started_at=started_at,
            completed_at=workflow.now(),
        )
