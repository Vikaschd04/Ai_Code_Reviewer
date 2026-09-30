"""AI run activities and workflow (P03; ADR 0012).

``prepare`` re-checks the project's AI policy, provider configuration and monthly limits right
before any source is disclosed. ``execute`` runs the bounded investigation against a read-only,
database-backed view of the run's snapshot, records every provider call's usage immediately (so
the monthly cap stays accurate even if the process dies), verifies every citation against the
snapshot and stores results. It is never retried automatically: a retry would repeat paid calls,
so an interrupted run ends FAILED and can be started again by the user.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    import httpx
    from sqlalchemy import func, select, update
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from crp_analysis.ai.config import AiSetup, resolve
    from crp_analysis.ai.orchestrator import CallRecord, InvestigationResult, investigate
    from crp_analysis.ai.prompts import (
        PROMPT_VERSION,
        Task,
        files_task,
        finding_task,
        question_task,
    )
    from crp_analysis.ai.results import Anchor
    from crp_analysis.ai.snapshot import FileInfo, FindingSummary, Relation, SearchHit, SymbolInfo
    from crp_analysis.ai.tools import ToolExecutor
    from crp_analysis.ai.verify import AnchorCheck, check_anchors, evidence_class
    from crp_analysis.catalog import lookup
    from crp_core.artifacts import ArtifactKey, ArtifactStore
    from crp_core.config import Settings
    from crp_core.db.ai_usage import month_usage
    from crp_core.db.models import (
        AiCall,
        AiFinding,
        AiRun,
        CodeSymbol,
        FileEntry,
        Finding,
        GraphBuild,
        GraphEdge,
        GraphNode,
        ProjectAiPolicy,
        Scan,
    )
    from crp_core.db.session import transaction
    from crp_core.domain.states import AiEvidenceClass, AiRunKind, AiRunState, FileDisposition
    from crp_core.workflows.contracts import (
        AI_RUN_WORKFLOW_NAME,
        AiRunFinalize,
        AiRunInput,
        AiRunPlan,
        AiRunResult,
    )
    from crp_worker.progress import heartbeat

logger = logging.getLogger(__name__)
_SEARCH_BYTE_BUDGET = 24 * 1024 * 1024
_LINE_CACHE = 64
MIN_MONTHLY_TOKENS = 2000


class DbSnapshotReader:
    """``SnapshotReader`` over PostgreSQL and the artifact store, bound to one snapshot."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        store: ArtifactStore,
        snapshot_id: UUID,
        scan_id: UUID | None,
        max_file_bytes: int,
    ) -> None:
        self.snapshot_id = str(snapshot_id)
        self._snapshot = snapshot_id
        self._scan = scan_id
        self._sessions = sessions
        self._store = store
        self._max_bytes = max_file_bytes
        self._files: dict[str, FileInfo] | None = None
        self._entries: dict[str, UUID] = {}
        self._lines: dict[str, list[str]] = {}

    async def files(self) -> dict[str, FileInfo]:
        if self._files is None:
            async with transaction(self._sessions) as session:
                rows = (
                    await session.execute(
                        select(
                            FileEntry.id,
                            FileEntry.path,
                            FileEntry.blob_sha256,
                            FileEntry.language,
                            FileEntry.line_count,
                        )
                        .where(
                            FileEntry.snapshot_id == self._snapshot,
                            FileEntry.disposition == FileDisposition.ANALYZABLE.value,
                            FileEntry.blob_sha256.is_not(None),
                        )
                        .order_by(FileEntry.path)
                    )
                ).all()
            self._files = {}
            for entry_id, path, sha, language, line_count in rows:
                if sha is None:  # excluded by the query; keeps the type narrow
                    continue
                self._files[path] = FileInfo(path, sha, language, line_count or 0)
                self._entries[path] = entry_id
        return self._files

    async def lines(self, path: str) -> list[str]:
        cached = self._lines.get(path)
        if cached is not None:
            return cached
        info = (await self.files()).get(path)
        if info is None:
            raise KeyError(path)
        data = await asyncio.to_thread(
            self._store.read_bytes,
            ArtifactKey(f"blobs/{info.sha256[:2]}/{info.sha256}"),
            max_bytes=self._max_bytes,
        )
        lines = data.decode("utf-8", errors="replace").splitlines()
        if len(self._lines) >= _LINE_CACHE:
            self._lines.pop(next(iter(self._lines)))
        self._lines[path] = lines
        return lines

    async def search(self, query: str, *, path_prefix: str | None, limit: int) -> list[SearchHit]:
        needle = query.lower()
        hits: list[SearchHit] = []
        scanned = 0
        for path, info in (await self.files()).items():
            if path_prefix and not path.startswith(path_prefix):
                continue
            lines = await self.lines(path)
            scanned += sum(len(line) for line in lines)
            for number, line in enumerate(lines, start=1):
                if needle in line.lower():
                    hits.append(SearchHit(path, number, line))
                    if len(hits) >= limit:
                        return hits
            if scanned > _SEARCH_BYTE_BUDGET:
                break
            del info
        return hits

    async def symbols(self, path: str, *, limit: int) -> list[SymbolInfo]:
        await self.files()
        entry = self._entries.get(path)
        if entry is None:
            return []
        async with transaction(self._sessions) as session:
            rows = (
                await session.execute(
                    select(
                        CodeSymbol.kind, CodeSymbol.name, CodeSymbol.start_line, CodeSymbol.end_line
                    )
                    .where(CodeSymbol.file_entry_id == entry)
                    .order_by(CodeSymbol.start_line)
                    .limit(limit)
                )
            ).all()
        return [SymbolInfo(name, kind, path, start, end) for kind, name, start, end in rows]

    async def neighbors(self, path: str, *, limit: int) -> list[Relation]:
        await self.files()
        entry = self._entries.get(path)
        if entry is None:
            return []
        async with transaction(self._sessions) as session:
            build = await session.scalar(
                select(GraphBuild.id).where(
                    GraphBuild.snapshot_id == self._snapshot, GraphBuild.is_current.is_(True)
                )
            )
            if build is None:
                return []
            node_ids = (
                await session.scalars(
                    select(GraphNode.id).where(
                        GraphNode.build_id == build, GraphNode.file_entry_id == entry
                    )
                )
            ).all()
            if not node_ids:
                return []
            edges = (
                await session.execute(
                    select(
                        GraphEdge.source_node_id,
                        GraphEdge.target_node_id,
                        GraphEdge.relation,
                        GraphEdge.target_ref,
                        GraphEdge.classification,
                        GraphEdge.evidence_start_line,
                    )
                    .where(
                        GraphEdge.build_id == build,
                        (GraphEdge.source_node_id.in_(node_ids))
                        | (GraphEdge.target_node_id.in_(node_ids)),
                    )
                    .limit(limit)
                )
            ).all()
            ids = {e[0] for e in edges} | {e[1] for e in edges if e[1] is not None}
            labels = dict(
                (
                    await session.execute(
                        select(GraphNode.id, GraphNode.label).where(GraphNode.id.in_(ids))
                    )
                ).all()
            )
        return [
            Relation(
                labels.get(source, str(source)),
                relation,
                labels.get(target, ref) if target is not None else ref,
                classification,
                line,
            )
            for source, target, relation, ref, classification, line in edges
        ]

    async def findings(self, path: str | None, *, limit: int) -> list[FindingSummary]:
        scan = self._scan
        async with transaction(self._sessions) as session:
            if scan is None:
                scan = await session.scalar(
                    select(Scan.id)
                    .where(Scan.snapshot_id == self._snapshot)
                    .order_by(Scan.created_at.desc())
                    .limit(1)
                )
            if scan is None:
                return []
            query = (
                select(Finding, FileEntry.path)
                .join(FileEntry, FileEntry.id == Finding.file_entry_id)
                .where(Finding.scan_id == scan)
                .order_by(Finding.severity, FileEntry.path, Finding.start_line)
                .limit(limit)
            )
            if path is not None:
                query = query.where(FileEntry.path == path)
            rows = (await session.execute(query)).all()
        return [
            FindingSummary(
                str(f.id),
                f.title,
                f.severity,
                f.category,
                f.engine,
                f.rule_id,
                file_path,
                f.start_line,
                f.end_line,
                f.message,
            )
            for f, file_path in rows
        ]


def _anchor_payload(anchor: Anchor, check: AnchorCheck) -> dict[str, Any]:
    return {
        "path": anchor.path,
        "start_line": anchor.start_line,
        "end_line": anchor.end_line,
        "quote": anchor.quote[:600],
        "status": check.status.value,
        "sha256": check.sha256,
    }


class AiRunActivities:
    def __init__(
        self,
        settings: Settings,
        store: ArtifactStore,
        sessions: async_sessionmaker[AsyncSession],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._sessions = sessions
        self._transport = transport  # tests only: an in-process provider double

    def setup(self) -> AiSetup:
        return resolve(self._settings)

    async def _fail(self, run_id: UUID, state: AiRunState, code: str, message: str) -> AiRunPlan:
        async with transaction(self._sessions) as session:
            run = await session.get(AiRun, run_id, with_for_update=True)
            if run is not None and not AiRunState(run.state).is_terminal:
                run.state = state.value
                run.error_code = code
                run.error_message = message
                run.finished_at = datetime.now(UTC)
        return AiRunPlan(run_id=run_id, terminal=True)

    @activity.defn(name="ai.prepare")
    async def prepare(self, payload: AiRunInput) -> AiRunPlan:
        async with transaction(self._sessions) as session:
            run = await session.get(AiRun, payload.run_id, with_for_update=True)
            if run is None:
                raise RuntimeError("AI run not found")
            state = AiRunState(run.state)
            if state.is_terminal:
                return AiRunPlan(run_id=run.id, terminal=True)
            if state is AiRunState.RUNNING:  # a previous attempt died mid-run: never repeat spend
                run.state = AiRunState.FAILED.value
                run.error_code = "interrupted"
                run.error_message = "The run was interrupted (service restart). Start it again."
                run.finished_at = datetime.now(UTC)
                return AiRunPlan(run_id=run.id, terminal=True)
            if run.cancel_requested_at is not None:
                run.state = AiRunState.CANCELED.value
                run.finished_at = datetime.now(UTC)
                return AiRunPlan(run_id=run.id, terminal=True)
            policy = await session.get(ProjectAiPolicy, run.project_id)
            project_id = run.project_id
        if policy is None or not policy.enabled:
            return await self._fail(
                payload.run_id,
                AiRunState.FAILED,
                "ai_policy_disabled",
                "AI review is switched off for this project; no code was sent.",
            )
        setup = self.setup()
        if not setup.available:
            return await self._fail(
                payload.run_id,
                AiRunState.FAILED,
                "ai_unavailable",
                setup.reason or "AI unavailable",
            )
        async with transaction(self._sessions) as session:
            usage = await month_usage(session)
        if (
            setup.monthly_token_limit
            and usage.total_tokens + MIN_MONTHLY_TOKENS > setup.monthly_token_limit
        ):
            return await self._fail(
                payload.run_id,
                AiRunState.BUDGET_EXHAUSTED,
                "monthly_token_limit",
                "This month's AI token limit is used up; no code was sent.",
            )
        if (
            setup.monthly_cost_limit_usd is not None
            and usage.cost_usd is not None
            and usage.cost_usd >= setup.monthly_cost_limit_usd
        ):
            return await self._fail(
                payload.run_id,
                AiRunState.BUDGET_EXHAUSTED,
                "monthly_cost_limit",
                "This month's AI cost limit is used up; no code was sent.",
            )
        async with transaction(self._sessions) as session:
            run = await session.get(AiRun, payload.run_id, with_for_update=True)
            if run is None or AiRunState(run.state) is not AiRunState.QUEUED:
                return AiRunPlan(run_id=payload.run_id, terminal=True)
            run.state = AiRunState.RUNNING.value
            run.started_at = datetime.now(UTC)
        logger.info(
            "AI run started", extra={"run_id": str(payload.run_id), "project_id": str(project_id)}
        )
        return AiRunPlan(run_id=payload.run_id)

    async def _task(self, run: AiRun, reader: DbSnapshotReader) -> Task:
        kind = AiRunKind(run.kind)
        if kind is AiRunKind.QUESTION:
            return await question_task(reader, run.question or "")
        if kind is AiRunKind.FILE_REVIEW:
            return await files_task(reader, list(run.target_paths or []))
        async with transaction(self._sessions) as session:
            row = (
                await session.execute(
                    select(Finding, FileEntry.path)
                    .join(FileEntry, FileEntry.id == Finding.file_entry_id)
                    .where(Finding.id == run.finding_id)
                )
            ).first()
        if row is None:
            raise LookupError("the finding to review no longer exists")
        finding, path = row
        stored = finding.guidance or {}
        info = lookup(finding.engine, finding.rule_id, finding.engine_severity, finding.rule_url)
        explanation = str(stored.get("explanation") or info.explanation)
        recommendation = str(stored.get("recommendation") or info.recommendation)
        guidance = f"Why it matters: {explanation}\nHow to fix: {recommendation}"
        summary = FindingSummary(
            str(finding.id),
            finding.title,
            finding.severity,
            finding.category,
            finding.engine,
            finding.rule_id,
            path,
            finding.start_line,
            finding.end_line,
            finding.message,
        )
        return await finding_task(reader, summary, guidance)

    @activity.defn(name="ai.execute")
    async def execute(self, payload: AiRunInput) -> AiRunResult:
        async with transaction(self._sessions) as session:
            run = await session.get(AiRun, payload.run_id)
            if run is None or AiRunState(run.state) is not AiRunState.RUNNING:
                return AiRunResult(run_id=payload.run_id, state=run.state if run else "MISSING")
            policy = await session.get(ProjectAiPolicy, run.project_id)
            usage_now = await month_usage(session)
            session.expunge(run)
        setup = self.setup()
        reader = DbSnapshotReader(
            self._sessions,
            self._store,
            run.snapshot_id,
            run.scan_id,
            self._settings.intake_max_text_file_bytes,
        )
        try:
            task = await self._task(run, reader)
        except LookupError as exc:
            await self._fail(payload.run_id, AiRunState.FAILED, "target_missing", str(exc))
            return AiRunResult(run_id=payload.run_id, state=AiRunState.FAILED.value)
        allowance = (
            max(0, setup.monthly_token_limit - usage_now.total_tokens)
            if setup.monthly_token_limit
            else None
        )
        tools = ToolExecutor(reader, max_excerpt_lines=policy.max_excerpt_lines if policy else 120)
        client = setup.client(self._transport)

        async def cancelled() -> bool:
            heartbeat("AI investigation in progress")
            async with transaction(self._sessions) as session:
                requested = await session.scalar(
                    select(AiRun.cancel_requested_at).where(AiRun.id == payload.run_id)
                )
            return requested is not None

        async def record(call: CallRecord) -> None:
            async with transaction(self._sessions) as session:
                session.add(
                    AiCall(
                        run_id=payload.run_id,
                        sequence=call.sequence,
                        provider=client.provider,
                        model=call.model[:128],
                        status=call.status,
                        error_code=call.error_code,
                        input_tokens=call.usage.input_tokens,
                        output_tokens=call.usage.output_tokens,
                        cache_read_tokens=call.usage.cache_read_tokens,
                        cache_write_tokens=call.usage.cache_write_tokens,
                        usage_reported=call.usage.reported,
                        cost_usd=call.cost_usd,
                        latency_ms=call.latency_ms,
                        attempts=call.attempts,
                        request_id=(call.request_id or "")[:128] or None,
                    )
                )
            heartbeat(f"model call {call.sequence} recorded")

        result = await investigate(
            client,
            task,
            tools,
            setup.limits,
            setup.prices,
            token_allowance=allowance,
            cancelled=cancelled,
            on_call=record,
            keep_transcript=setup.keep_transcripts,
        )
        state = await self._store_result(payload.run_id, run, reader, result, setup)
        return AiRunResult(run_id=payload.run_id, state=state.value)

    async def _store_result(
        self,
        run_id: UUID,
        run: AiRun,
        reader: DbSnapshotReader,
        result: InvestigationResult,
        setup: AiSetup,
    ) -> AiRunState:
        answer: dict[str, Any] | None = None
        findings: list[AiFinding] = []
        limitations = [
            "Source-only AI review: the model read code excerpts; nothing was built or executed.",
        ]
        if result.limitation:
            limitations.append(result.limitation)
        if result.answer is not None:
            checks = await check_anchors(reader, result.answer.citations)
            evidence = evidence_class(checks)
            answer = {
                "type": "answer",
                "text": result.answer.answer,
                "abstained": result.answer.abstained,
                "uncertainty": result.answer.uncertainty,
                "inferred_intent": result.answer.inferred_intent,
                "evidence_class": evidence.value,
                "citations": [
                    _anchor_payload(a, c)
                    for a, c in zip(result.answer.citations, checks, strict=True)
                ],
            }
            if evidence is AiEvidenceClass.REJECTED:
                limitations.append(
                    "None of the answer's citations matched the code; treat it as unverified."
                )
        if result.review is not None:
            review = result.review
            answer = {
                "type": "review",
                "text": review.summary,
                "reviewed_paths": review.reviewed_paths,
            }
            if review.not_reviewed:
                limitations.append(f"Not reviewed: {review.not_reviewed}")
            if review.finding_assessment is not None:
                checks = await check_anchors(reader, review.finding_assessment.anchors)
                answer["assessment"] = {
                    "verdict": review.finding_assessment.verdict,
                    "explanation": review.finding_assessment.explanation,
                    "evidence_class": evidence_class(checks).value,
                    "anchors": [
                        _anchor_payload(a, c)
                        for a, c in zip(review.finding_assessment.anchors, checks, strict=True)
                    ],
                }
            for number, item in enumerate(review.findings, start=1):
                checks = await check_anchors(reader, item.anchors)
                related = None
                if item.related_finding_id:
                    try:
                        related = UUID(item.related_finding_id)
                    except ValueError:
                        related = None
                findings.append(
                    AiFinding(
                        run_id=run_id,
                        sequence=number,
                        title=item.title,
                        category=item.category.value,
                        severity=item.severity.value,
                        severity_rationale=item.severity_rationale,
                        confidence=item.confidence.value,
                        evidence_class=evidence_class(checks).value,
                        anchors=[
                            _anchor_payload(a, c) for a, c in zip(item.anchors, checks, strict=True)
                        ],
                        triggering_conditions=item.triggering_conditions,
                        impact=item.impact,
                        recommendation=item.recommendation,
                        validation_needed=item.validation_needed or None,
                        uncertainty=item.uncertainty or None,
                        related_finding_id=related,
                        verification={
                            "checked_at": datetime.now(UTC).isoformat(),
                            "anchors": [c.as_dict() for c in checks],
                        },
                    )
                )
        state = {
            "submitted": AiRunState.SUCCEEDED,
            "budget_exhausted": AiRunState.BUDGET_EXHAUSTED,
            "timeout": AiRunState.BUDGET_EXHAUSTED,
            "cancelled": AiRunState.CANCELED,
            "provider_error": AiRunState.FAILED,
            "no_result": AiRunState.FAILED,
        }[result.stop]
        if state is AiRunState.SUCCEEDED and result.limitation:
            state = AiRunState.PARTIAL
        usage = result.usage
        summary: dict[str, object] = {
            "calls": len(result.calls),
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "usage_reported": usage.reported,
            "budget_tokens": result.budget_tokens,
            "cost_usd": result.cost_usd if setup.prices.known else None,
            "excerpts": len(result.excerpts),
        }
        if setup.keep_transcripts and result.transcript:
            key = ArtifactKey(f"ai-runs/{run_id.hex}/transcript.json")
            data = json.dumps(
                {"prompt_version": PROMPT_VERSION, "messages": result.transcript}
            ).encode()
            try:
                await asyncio.to_thread(self._store.put_bytes, key, data, overwrite=True)
            except Exception:
                logger.exception("could not store AI transcript", extra={"run_id": str(run_id)})
        async with transaction(self._sessions) as session:
            stored = await session.get(AiRun, run_id, with_for_update=True)
            if stored is None:
                return AiRunState.FAILED
            stored.state = state.value
            stored.finished_at = datetime.now(UTC)
            stored.answer = answer
            stored.steps = result.steps
            stored.limitations = limitations
            stored.usage = summary
            if result.error is not None:
                stored.error_code = f"provider_{result.error.code.value}"
                stored.error_message = str(result.error)[:500]
            elif result.stop == "no_result":
                stored.error_code = "no_result"
                stored.error_message = result.limitation
            session.add_all(findings)
        logger.info(
            "AI run finished",
            extra={
                "run_id": str(run_id),
                "state": state.value,
                "calls": len(result.calls),
                "tokens": usage.total_tokens,
            },
        )
        return state

    @activity.defn(name="ai.finalize")
    async def finalize(self, payload: AiRunFinalize) -> AiRunResult:
        async with transaction(self._sessions) as session:
            run = await session.get(AiRun, payload.run_id, with_for_update=True)
            if run is None:
                return AiRunResult(run_id=payload.run_id, state="MISSING")
            if not AiRunState(run.state).is_terminal:
                if payload.canceled:
                    run.state = AiRunState.CANCELED.value
                else:
                    run.state = AiRunState.FAILED.value
                    run.error_code = "interrupted"
                    run.error_message = "The run was interrupted. Start it again."
                run.finished_at = datetime.now(UTC)
            return AiRunResult(run_id=run.id, state=run.state)

    def all(self) -> list[object]:
        return [self.prepare, self.execute, self.finalize]


async def fail_interrupted_runs(sessions: async_sessionmaker[AsyncSession]) -> int:
    """Lite restarts: runs left RUNNING are failed (never re-executed: that would re-spend)."""
    async with transaction(sessions) as session:
        result = await session.execute(
            update(AiRun)
            .where(AiRun.state == AiRunState.RUNNING.value)
            .values(
                state=AiRunState.FAILED.value,
                error_code="interrupted",
                error_message="The run was interrupted (service restart). Start it again.",
                finished_at=func.now(),
            )
            .returning(AiRun.id)
        )
        return len(result.all())


@workflow.defn(name=AI_RUN_WORKFLOW_NAME)
class AiRunWorkflow:
    @workflow.run
    async def run(self, payload: AiRunInput) -> AiRunResult:
        try:
            plan = await workflow.execute_activity(
                "ai.prepare",
                payload,
                result_type=AiRunPlan,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            if plan.terminal:
                return AiRunResult(run_id=payload.run_id, state="TERMINAL")
            result: AiRunResult = await workflow.execute_activity(
                "ai.execute",
                payload,
                result_type=AiRunResult,
                start_to_close_timeout=timedelta(hours=1),
                heartbeat_timeout=timedelta(minutes=3),
                cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                retry_policy=RetryPolicy(maximum_attempts=1),  # never repeat paid calls
            )
        except (asyncio.CancelledError, ActivityError) as exc:
            # A cancel can surface as either exception; anything else ends the run FAILED
            # (never retried). finalize() leaves runs that already reached a final state alone.
            canceled = is_cancelled_exception(exc)
            final: AiRunResult = await workflow.execute_activity(
                "ai.finalize",
                AiRunFinalize(run_id=payload.run_id, canceled=canceled, interrupted=not canceled),
                result_type=AiRunResult,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
            return final
        return result
