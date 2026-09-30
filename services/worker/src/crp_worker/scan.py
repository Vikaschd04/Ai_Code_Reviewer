"""Scan workflow: plan engines, run them in isolated work directories, publish results, finalize.

Each engine activity publishes its engine-run outcome, per-file coverage and normalized findings
in one transaction (retries first delete that run's previous partial publication). A failed or
unavailable engine never hides completed results from other engines, and never reads as clean.
Per-file engines reuse compatible cached results (see ``engine_cache``); the graph extractor
publishes a superseding snapshot graph build; finalize applies the issue lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.workflow import ActivityCancellationType

with workflow.unsafe.imports_passed_through():
    from sqlalchemy import delete, select, update
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from crp_analysis import structure
    from crp_analysis.engines.base import (
        CancelToken,
        EngineAdapter,
        EngineOutcome,
        completed_state,
    )
    from crp_analysis.engines.eslint import EslintAdapter
    from crp_analysis.engines.frameworks import FrameworkRulesAdapter
    from crp_analysis.engines.opengrep import OpengrepAdapter
    from crp_analysis.engines.pmd import APEX, PmdAdapter
    from crp_analysis.engines.trivy import TrivyAdapter
    from crp_analysis.graph.extract import FileFacts, graph_extractor_version
    from crp_analysis.normalize import NormalizedFinding, normalize
    from crp_analysis.workspace import WorkFile, materialized
    from crp_core.artifacts import ArtifactKey, ArtifactStore
    from crp_core.config import Settings
    from crp_core.db.models import (
        CodeSymbol,
        EngineRun,
        FileCoverage,
        FileEntry,
        Finding,
        GraphBuild,
        Scan,
        ScanEvent,
    )
    from crp_core.db.session import transaction
    from crp_core.domain.states import (
        CacheMode,
        CoverageOutcome,
        EngineState,
        FileDisposition,
        FindingStatus,
        ScanState,
        require_scan_transition,
    )
    from crp_core.workflows.contracts import (
        ENGINE_NAMES,
        EXTRACTOR_NAMES,
        SCAN_WORKFLOW_NAME,
        EngineTask,
        EngineTaskResult,
        FinalizeInput,
        ScanPlan,
        ScanWorkflowInput,
        ScanWorkflowResult,
    )
    from crp_worker import engine_cache, graph_job
    from crp_worker.lifecycle import apply_lifecycle
    from crp_worker.progress import heartbeat

logger = logging.getLogger(__name__)

TERMINAL_ENGINE = frozenset(
    {
        EngineState.SUCCEEDED,
        EngineState.PARTIAL,
        EngineState.FAILED,
        EngineState.CANCELED,
        EngineState.UNAVAILABLE,
        EngineState.NOT_APPLICABLE,
    }
)
_COMPLETED = frozenset({EngineState.SUCCEEDED, EngineState.PARTIAL})
CACHED_REASON = "reused cached result (identical content, engine, rules and configuration)"


@dataclass(frozen=True, slots=True)
class EligibleFile:
    entry_id: UUID
    path: str
    sha256: str
    language: str | None


@dataclass(slots=True)
class StructureFileResult:
    entry_id: UUID
    status: str
    errors: int
    message: str | None
    symbols: list[structure.ExtractedSymbol]


def _merge_cached(
    outcome: EngineOutcome,
    normalized: list[NormalizedFinding],
    hits: dict[str, list[NormalizedFinding]],
    ran: bool,
) -> tuple[EngineOutcome, list[NormalizedFinding]]:
    """Combine a fresh run over cache misses with reused per-file results."""
    if not hits:
        return outcome, normalized
    outcome.attempted = sorted(set(outcome.attempted) | set(hits))
    merged = normalized + [f for path in sorted(hits) for f in hits[path]]
    if outcome.state is EngineState.CANCELED:
        return outcome, merged
    if ran and outcome.state in {EngineState.FAILED, EngineState.UNAVAILABLE}:
        outcome.state = EngineState.PARTIAL  # the fresh part failed; reused results stay valid
        return outcome, merged
    failed = sum(1 for p in outcome.problems if p.outcome == CoverageOutcome.FAILED.value)
    state = completed_state(len(outcome.attempted), failed)
    if state is EngineState.SUCCEEDED and any(
        p.outcome == CoverageOutcome.NOT_ATTEMPTED.value for p in outcome.problems
    ):
        state = EngineState.PARTIAL
    outcome.state = state
    return outcome, merged


CacheContext = tuple["engine_cache.CacheScope | None", dict[str, str], set[str], bool]


def default_adapters(settings: Settings) -> dict[str, EngineAdapter]:
    """The trusted engines, configured from settings (shared by scans and fix validation)."""
    return {
        "pmd": PmdAdapter(
            settings.pmd_home,
            java_heap=settings.pmd_java_heap,
            timeout_seconds=settings.engine_timeout_seconds,
            max_output_bytes=settings.engine_max_output_bytes,
        ),
        "eslint": EslintAdapter(
            settings.eslint_runner_dir,
            node_executable=settings.node_executable,
            timeout_seconds=settings.engine_timeout_seconds,
            max_output_bytes=settings.engine_max_output_bytes,
            heap_mb=settings.eslint_heap_mb,
        ),
        "opengrep": OpengrepAdapter(
            settings.opengrep_home,
            timeout_seconds=settings.engine_timeout_seconds,
            max_output_bytes=settings.engine_max_output_bytes,
            max_target_bytes=settings.intake_max_text_file_bytes,
            jobs=settings.opengrep_jobs,
        ),
        "trivy": TrivyAdapter(
            settings.trivy_home,
            settings.trivy_cache_dir,
            timeout_seconds=settings.engine_timeout_seconds,
            max_output_bytes=settings.engine_max_output_bytes,
        ),
        # Framework packs (P04): Apex rules on the same pinned PMD, configuration checks.
        "pmd-apex": PmdAdapter(
            settings.pmd_home,
            java_heap=settings.pmd_java_heap,
            timeout_seconds=settings.engine_timeout_seconds,
            max_output_bytes=settings.engine_max_output_bytes,
            ruleset=APEX,
        ),
        "frameworks": FrameworkRulesAdapter(settings.intake_max_text_file_bytes),
    }


class ScanActivities:
    def __init__(
        self,
        settings: Settings,
        store: ArtifactStore,
        session_factory: async_sessionmaker[AsyncSession],
        adapters: dict[str, EngineAdapter] | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._sessions = session_factory
        defaults = default_adapters(settings)
        self._adapters: dict[str, EngineAdapter] = {**defaults, **(adapters or {})}

    # -- helpers -------------------------------------------------------------------------------

    def _eligible(self, engine: str, entry: FileEntry) -> bool:
        if engine in EXTRACTOR_NAMES:
            return structure.grammar_for(entry.path, entry.language) is not None
        return self._adapters[engine].is_eligible(entry.path, entry.language)

    async def _event(
        self, session: AsyncSession, scan_id: UUID, kind: str, **payload: object
    ) -> None:
        session.add(ScanEvent(scan_id=scan_id, kind=kind, payload=payload))

    async def _eligible_files(
        self, session: AsyncSession, scan: Scan, engine: str
    ) -> list[EligibleFile]:
        rows = (
            await session.execute(
                select(FileEntry).where(
                    FileEntry.snapshot_id == scan.snapshot_id,
                    FileEntry.disposition == FileDisposition.ANALYZABLE.value,
                )
            )
        ).scalars()
        return [
            EligibleFile(row.id, row.path, row.blob_sha256 or "", row.language)
            for row in rows
            if self._eligible(engine, row)
        ]

    # -- prepare -------------------------------------------------------------------------------

    @activity.defn(name="scan.prepare")
    async def prepare(self, payload: ScanWorkflowInput) -> ScanPlan:
        async with transaction(self._sessions) as session:
            scan = await session.get(Scan, payload.scan_id, with_for_update=True)
            if scan is None:
                raise RuntimeError("scan not found")
            state = ScanState(scan.state)
            if state.is_terminal or state is ScanState.RUNNING:
                runs = (
                    await session.execute(select(EngineRun).where(EngineRun.scan_id == scan.id))
                ).scalars()
                pending = [r.engine for r in runs if EngineState(r.state) not in TERMINAL_ENGINE]
                return ScanPlan(scan_id=scan.id, engines=pending, terminal=state.is_terminal)
            if scan.cancel_requested_at is not None:
                scan.state = require_scan_transition(state, ScanState.CANCELED).value
                scan.finished_at = datetime.now(UTC)
                await self._event(session, scan.id, "scan_finished", state="CANCELED")
                return ScanPlan(scan_id=scan.id, engines=[], terminal=True)
            scan.state = require_scan_transition(state, ScanState.RUNNING).value
            scan.started_at = datetime.now(UTC)
            planned: list[str] = []
            plan_summary: dict[str, object] = {}
            for engine in ENGINE_NAMES:
                files = await self._eligible_files(session, scan, engine)
                run = EngineRun(scan_id=scan.id, engine=engine, files_eligible=len(files))
                if engine == "structure":
                    run.engine_version = structure.extractor_version()
                    run.ruleset_id = "syntax-structure-v1"
                    availability_reason = None
                    available = True
                elif engine == "graph":
                    run.engine_version = graph_extractor_version()[:128]
                    run.ruleset_id = "crp-graph-v1"
                    availability_reason = None
                    available = True
                else:
                    adapter = self._adapters[engine]
                    availability = adapter.availability()
                    run.engine_version = availability.version
                    run.ruleset_id = adapter.ruleset_id
                    available = availability.available
                    availability_reason = availability.reason
                    rules = adapter.enabled_rules()
                    run.enabled_rules = list(rules) if rules is not None else None
                    identity = adapter.cache_identity() if available else None
                    run.config_fingerprint = identity.config_fingerprint if identity else None
                if not files:
                    run.state = EngineState.NOT_APPLICABLE.value
                elif not available:
                    run.state = EngineState.UNAVAILABLE.value
                    run.error_code = "engine_unavailable"
                    run.error_message = availability_reason
                    run.finished_at = datetime.now(UTC)
                else:
                    run.state = EngineState.QUEUED.value
                    planned.append(engine)
                session.add(run)
                await session.flush()
                if run.state == EngineState.UNAVAILABLE.value:
                    session.add_all(
                        FileCoverage(
                            engine_run_id=run.id,
                            file_entry_id=f.entry_id,
                            outcome=CoverageOutcome.NOT_ATTEMPTED.value,
                            reason=f"engine unavailable: {availability_reason}",
                        )
                        for f in files
                    )
                plan_summary[engine] = {"state": run.state, "eligible": len(files)}
            await self._event(session, scan.id, "scan_started", engines=plan_summary)
            return ScanPlan(scan_id=scan.id, engines=planned)

    # -- engine --------------------------------------------------------------------------------

    @activity.defn(name="scan.engine")
    async def run_engine(self, task: EngineTask) -> EngineTaskResult:
        async with transaction(self._sessions) as session:
            scan = await session.get(Scan, task.scan_id)
            run = (
                await session.execute(
                    select(EngineRun).where(
                        EngineRun.scan_id == task.scan_id, EngineRun.engine == task.engine
                    )
                )
            ).scalar_one()
            if EngineState(run.state) in TERMINAL_ENGINE:
                return EngineTaskResult(
                    engine=task.engine, state=run.state, findings=run.findings_count
                )
            if scan is None:
                raise RuntimeError("scan not found")
            files = await self._eligible_files(session, scan, task.engine)
            run.state = EngineState.RUNNING.value
            run.started_at = datetime.now(UTC)
            run_id, snapshot_id = run.id, scan.snapshot_id
            meta = {
                "workspace_id": scan.workspace_id,
                "project_id": scan.project_id,
                "snapshot_id": scan.snapshot_id,
            }
            refresh = scan.cache_mode == CacheMode.REFRESH.value
            scope = self._cache_scope(task.engine, meta, run.engine_version)
            keys = {f.path: scope.key(f.path, f.sha256) for f in files} if scope else {}
            hits: dict[str, list[NormalizedFinding]] = {}
            facts_hits: dict[str, FileFacts] = {}
            if scope is not None and not refresh:
                if task.engine == "graph":
                    payloads = await engine_cache.load_payloads(session, scope.project_id, keys)
                    facts_hits = {
                        path: FileFacts.from_json(p["facts"])  # type: ignore[arg-type]
                        for path, p in payloads.items()
                        if isinstance(p.get("facts"), dict)
                    }
                else:
                    hits = await engine_cache.load(session, scope, keys)
            snapshot_files = (
                await self._snapshot_files(session, scan) if task.engine == "graph" else []
            )
            await self._event(
                session, task.scan_id, "engine_started", engine=task.engine, files=len(files)
            )

        cancel = CancelToken()
        if task.engine == "structure":
            results = await self._supervise(
                lambda: self._structure(files, cancel), cancel, run_id, task.engine
            )
            return await self._publish_structure(task, run_id, snapshot_id, files, results)
        if task.engine == "graph":
            sources = [
                graph_job.SnapshotFile(f.entry_id, f.path, f.sha256, f.language, "ANALYZABLE")
                for f in files
            ]
            graph_run = await self._supervise(
                lambda: graph_job.run_graph(
                    self._store,
                    sources,
                    snapshot_files,
                    facts_hits,
                    max_file_bytes=self._settings.intake_max_text_file_bytes,
                    cancel=cancel,
                ),
                cancel,
                run_id,
                task.engine,
            )
            return await self._publish_graph(
                task, run_id, meta, sources, snapshot_files, graph_run, scope, keys
            )
        misses = [f for f in files if f.path not in hits]
        adapter = self._adapters[task.engine]
        if misses:
            work_dir = self._settings.work_root / "scans" / task.scan_id.hex / task.engine
            outcome, normalized = await self._supervise(
                lambda: self._run_adapter(task.engine, misses, work_dir, cancel),
                cancel,
                run_id,
                task.engine,
            )
        else:
            identity = adapter.cache_identity()
            outcome = EngineOutcome(
                state=EngineState.SUCCEEDED,
                engine_version=scope.engine_version if scope else None,
                ruleset_id=adapter.ruleset_id,
                ruleset_sha256=identity.ruleset_sha256 if identity else None,
            )
            normalized = []
        outcome, normalized = _merge_cached(outcome, normalized, hits, bool(misses))
        return await self._publish_engine(
            task,
            run_id,
            snapshot_id,
            meta,
            files,
            outcome,
            normalized,
            cache=(scope, keys, set(hits), refresh),
        )

    def _cache_scope(
        self, engine: str, meta: dict[str, UUID], version: str | None
    ) -> engine_cache.CacheScope | None:
        if engine == "structure" or version is None:
            return None
        identity = (
            graph_job.graph_cache_identity()
            if engine == "graph"
            else self._adapters[engine].cache_identity()
        )
        if identity is None:
            return None
        return engine_cache.CacheScope(
            meta["workspace_id"],
            meta["project_id"],
            engine,
            version,
            identity,
            self._settings.intake_max_text_file_bytes,
        )

    async def _snapshot_files(
        self, session: AsyncSession, scan: Scan
    ) -> list[graph_job.SnapshotFile]:
        rows = (
            await session.execute(
                select(FileEntry).where(FileEntry.snapshot_id == scan.snapshot_id)
            )
        ).scalars()
        return [
            graph_job.SnapshotFile(r.id, r.path, r.blob_sha256, r.language, r.disposition)
            for r in rows
        ]

    async def _supervise[T](
        self, work: Callable[[], T], cancel: CancelToken, run_id: UUID, label: str
    ) -> T:
        """Run blocking engine work in a thread, heartbeating; propagate cancellation into it."""
        job = asyncio.ensure_future(asyncio.to_thread(work))
        try:
            while not job.done():
                heartbeat(f"{label} running")
                await asyncio.wait({job}, timeout=1)
            return job.result()
        except asyncio.CancelledError:
            cancel.cancel()
            await asyncio.shield(asyncio.wait({job}, timeout=30))
            await self._mark_run(run_id, EngineState.CANCELED, "canceled", "Scan canceled by user")
            raise

    def _run_adapter(
        self, engine: str, files: list[EligibleFile], work_dir: Path, cancel: CancelToken
    ) -> tuple[EngineOutcome, list[NormalizedFinding]]:
        adapter = self._adapters[engine]
        work = [WorkFile(f.path, f.sha256) for f in files]
        with materialized(
            self._store, work_dir, work, max_file_bytes=self._settings.intake_max_text_file_bytes
        ) as root:
            outcome = adapter.run(
                root, [f.path for f in files], cancel=cancel, heartbeat=lambda _m: None
            )
            normalized = normalize(engine, root, outcome.findings)
        return outcome, normalized

    def _structure(
        self, files: list[EligibleFile], cancel: CancelToken
    ) -> list[StructureFileResult]:
        results: list[StructureFileResult] = []
        limit = self._settings.structure_max_symbols_per_file
        for item in files:
            if cancel.cancelled:
                break
            data = self._store.read_bytes(
                ArtifactKey(f"blobs/{item.sha256[:2]}/{item.sha256}"),
                max_bytes=self._settings.intake_max_text_file_bytes,
            )
            extracted = structure.extract(data, item.path, item.language, max_symbols=limit)
            message = extracted.message
            if extracted.truncated:
                message = f"{message or ''} symbols truncated at {limit}".strip()
            results.append(
                StructureFileResult(
                    item.entry_id,
                    extracted.status,
                    extracted.error_count,
                    message,
                    extracted.symbols,
                )
            )
        return results

    async def _mark_run(self, run_id: UUID, state: EngineState, code: str, message: str) -> None:
        async with transaction(self._sessions) as session:
            await session.execute(
                update(EngineRun)
                .where(EngineRun.id == run_id, EngineRun.state.in_(["QUEUED", "RUNNING"]))
                .values(
                    state=state.value,
                    error_code=code,
                    error_message=message,
                    finished_at=datetime.now(UTC),
                )
            )

    async def _publish_structure(
        self,
        task: EngineTask,
        run_id: UUID,
        snapshot_id: UUID,
        files: list[EligibleFile],
        results: list[StructureFileResult],
    ) -> EngineTaskResult:
        by_id = {r.entry_id: r for r in results}
        failed = sum(1 for r in results if r.status == "FAILED")
        partial = sum(1 for r in results if r.status == "PARTIAL")
        async with transaction(self._sessions) as session:
            await session.execute(delete(FileCoverage).where(FileCoverage.engine_run_id == run_id))
            await session.execute(delete(CodeSymbol).where(CodeSymbol.snapshot_id == snapshot_id))
            for item in files:
                result = by_id.get(item.entry_id)
                reason: str | None
                if result is None:
                    outcome, reason = CoverageOutcome.NOT_ATTEMPTED, "not processed"
                elif result.status == "FAILED":
                    outcome, reason = CoverageOutcome.FAILED, result.message
                else:
                    outcome, reason = CoverageOutcome.ANALYZED, result.message
                session.add(
                    FileCoverage(
                        engine_run_id=run_id,
                        file_entry_id=item.entry_id,
                        outcome=outcome.value,
                        reason=reason,
                    )
                )
                if result is not None:
                    await session.execute(
                        update(FileEntry)
                        .where(FileEntry.id == item.entry_id)
                        .values(parse_status=result.status, parse_error_count=result.errors)
                    )
                    session.add_all(
                        CodeSymbol(
                            snapshot_id=snapshot_id,
                            file_entry_id=item.entry_id,
                            kind=s.kind,
                            name=s.name,
                            container=s.container,
                            start_line=s.start_line,
                            start_column=s.start_column,
                            end_line=s.end_line,
                            end_column=s.end_column,
                            extractor=structure.EXTRACTOR,
                        )
                        for s in result.symbols
                    )
            attempted = len(results)
            state = (
                EngineState.SUCCEEDED
                if failed == 0 and attempted == len(files)
                else EngineState.PARTIAL
            )
            if attempted and failed == attempted:
                state = EngineState.FAILED
            run = await session.get(EngineRun, run_id)
            if run is None:
                raise RuntimeError("engine run disappeared during publication")
            run.state = state.value
            run.files_attempted = attempted
            run.files_failed = failed
            run.files_succeeded = attempted - failed
            run.finished_at = datetime.now(UTC)
            run.diagnostics = {
                "partial_parses": partial,
                "symbols": sum(len(r.symbols) for r in results),
                "note": "Syntax-level extraction only; no type or cross-file resolution.",
            }
            await self._event(
                session,
                task.scan_id,
                "engine_finished",
                engine=task.engine,
                state=state.value,
                findings=0,
            )
        return EngineTaskResult(engine=task.engine, state=state.value)

    async def _publish_graph(
        self,
        task: EngineTask,
        run_id: UUID,
        meta: dict[str, UUID],
        sources: list[graph_job.SnapshotFile],
        snapshot_files: list[graph_job.SnapshotFile],
        graph_run: graph_job.GraphRun,
        scope: engine_cache.CacheScope | None,
        keys: dict[str, str],
    ) -> EngineTaskResult:
        if graph_run.canceled:
            async with transaction(self._sessions) as session:
                await graph_job.publish_failed(
                    session, scope=meta, scan_id=task.scan_id, reason="graph extraction canceled"
                )
            await self._mark_run(run_id, EngineState.CANCELED, "canceled", "Scan canceled")
            return EngineTaskResult(engine=task.engine, state=EngineState.CANCELED.value)
        state = graph_job.build_state(sources, graph_run)
        cached = {s.path for s in sources if s.path in graph_run.facts} - set(graph_run.fresh)
        async with transaction(self._sessions) as session:
            await session.execute(delete(FileCoverage).where(FileCoverage.engine_run_id == run_id))
            build = await graph_job.publish(
                session,
                scope=meta,
                scan_id=task.scan_id,
                sources=sources,
                snapshot=snapshot_files,
                run=graph_run,
                state=state,
                cache_hits=len(cached),
            )
            failed = 0
            for item in sources:
                facts = graph_run.facts.get(item.path)
                reason: str | None
                if facts is None:
                    outcome, reason = CoverageOutcome.NOT_ATTEMPTED, "not processed"
                elif facts.status == "FAILED":
                    outcome, reason = CoverageOutcome.FAILED, facts.message
                    failed += 1
                else:
                    outcome, reason = CoverageOutcome.ANALYZED, facts.message
                session.add(
                    FileCoverage(
                        engine_run_id=run_id,
                        file_entry_id=item.entry_id,
                        outcome=outcome.value,
                        reason=CACHED_REASON if item.path in cached and not reason else reason,
                        cached=item.path in cached,
                    )
                )
            if scope is not None:
                shas = {s.path: s.sha256 or "" for s in sources}
                await engine_cache.store_payloads(
                    session,
                    scope,
                    {
                        path: (keys[path], shas[path], {"facts": facts.to_json()})
                        for path, facts in graph_run.fresh.items()
                        if facts.status in {"OK", "PARTIAL"} and path in keys
                    },
                )
                await engine_cache.touch(session, scope.project_id, (keys[p] for p in cached))
            attempted = sum(1 for s in sources if s.path in graph_run.facts)
            run = await session.get(EngineRun, run_id)
            if run is None:
                raise RuntimeError("engine run disappeared during publication")
            run.state = EngineState(state.value).value
            run.files_attempted = attempted
            run.files_failed = failed
            run.files_succeeded = attempted - failed
            run.cache_hits = len(cached)
            run.cache_misses = len(graph_run.fresh)
            run.finished_at = datetime.now(UTC)
            run.diagnostics = {
                "build_id": str(build.id),
                "nodes": build.node_count,
                "edges": build.edge_count,
                "unresolved_edges": build.unresolved_count,
                "edges_by_classification": graph_run.data.diagnostics.get(
                    "edges_by_classification", {}
                ),
                "notes": len(graph_run.notes),
                "note": "Syntax-level relations with deterministic resolution; unresolved "
                "edges are listed, never guessed.",
            }
            await self._event(
                session,
                task.scan_id,
                "engine_finished",
                engine=task.engine,
                state=run.state,
                findings=0,
            )
        return EngineTaskResult(engine=task.engine, state=run.state)

    async def _publish_engine(
        self,
        task: EngineTask,
        run_id: UUID,
        snapshot_id: UUID,
        meta: dict[str, UUID],
        files: list[EligibleFile],
        outcome: EngineOutcome,
        normalized: list[NormalizedFinding],
        cache: CacheContext | None = None,
    ) -> EngineTaskResult:
        scope, keys, cached_paths, refresh = cache or (None, {}, set(), False)
        raw_key = raw_sha = None
        if outcome.raw_report is not None:
            key = ArtifactKey(f"scans/{task.scan_id.hex}/raw/{task.engine}.json")
            ref = await asyncio.to_thread(
                self._store.put_bytes, key, outcome.raw_report, overwrite=True
            )
            raw_key, raw_sha = str(key), ref.sha256
        ids = {f.path: f.entry_id for f in files}
        problems = {p.path: p for p in outcome.problems}
        attempted = set(outcome.attempted)
        async with transaction(self._sessions) as session:
            await session.execute(delete(Finding).where(Finding.engine_run_id == run_id))
            await session.execute(delete(FileCoverage).where(FileCoverage.engine_run_id == run_id))
            for item in files:
                problem = problems.get(item.path)
                if item.path in cached_paths:
                    outcome_value, reason = CoverageOutcome.ANALYZED.value, CACHED_REASON
                elif problem is not None:
                    outcome_value, reason = problem.outcome, problem.reason
                elif item.path in attempted:
                    outcome_value, reason = CoverageOutcome.ANALYZED.value, None
                else:
                    outcome_value, reason = (
                        CoverageOutcome.NOT_ATTEMPTED.value,
                        "not reported by engine",
                    )
                session.add(
                    FileCoverage(
                        engine_run_id=run_id,
                        file_entry_id=item.entry_id,
                        outcome=outcome_value,
                        reason=reason,
                        cached=item.path in cached_paths,
                    )
                )
            seen: set[str] = set()
            for found in normalized:
                entry_id = ids.get(found.raw.path)
                if entry_id is None or found.fingerprint in seen:
                    continue
                seen.add(found.fingerprint)
                session.add(
                    Finding(
                        workspace_id=meta["workspace_id"],
                        project_id=meta["project_id"],
                        snapshot_id=snapshot_id,
                        scan_id=task.scan_id,
                        engine_run_id=run_id,
                        file_entry_id=entry_id,
                        fingerprint=found.fingerprint,
                        engine=task.engine,
                        engine_version=outcome.engine_version or "unknown",
                        rule_id=found.raw.rule_id,
                        ruleset=found.raw.ruleset,
                        engine_severity=found.raw.engine_severity,
                        severity=found.severity,
                        category=found.category,
                        confidence="deterministic_rule",
                        title=found.title[:300],
                        message=found.raw.message[:4000],
                        anchor_kind=found.anchor,
                        start_line=found.start_line,
                        start_column=found.raw.start_column if found.start_line else None,
                        end_line=found.end_line,
                        end_column=found.raw.end_column if found.start_line else None,
                        rule_url=found.rule_url,
                        status=FindingStatus.OPEN.value,
                        in_catalog=found.in_catalog,
                        correlation_key=found.correlation_key,
                        rule_family=found.family,
                        details=found.raw.details,
                        guidance=asdict(found.raw.guidance) if found.raw.guidance else None,
                    )
                )
            failed = sum(1 for p in outcome.problems if p.outcome == CoverageOutcome.FAILED.value)
            run = await session.get(EngineRun, run_id)
            if run is None:
                raise RuntimeError("engine run disappeared during publication")
            run.state = outcome.state.value
            run.engine_version = outcome.engine_version or run.engine_version
            run.ruleset_id = outcome.ruleset_id or run.ruleset_id
            run.ruleset_sha256 = outcome.ruleset_sha256 or run.ruleset_sha256
            run.files_attempted = len(attempted)
            run.files_failed = min(failed, len(attempted))
            run.files_succeeded = len(attempted) - run.files_failed
            run.findings_count = len(seen)
            run.exit_code = outcome.exit_code
            run.duration_ms = outcome.duration_ms
            run.raw_artifact_key = raw_key
            run.raw_artifact_sha256 = raw_sha
            run.error_code = outcome.error_code
            run.error_message = outcome.error_message
            run.cache_hits = len(cached_paths)
            run.cache_misses = len(files) - len(cached_paths) if scope else 0
            run.diagnostics = {
                **outcome.diagnostics,
                "cache": {
                    "mode": "refresh" if refresh else "use",
                    "eligible": scope is not None,
                    "hits": len(cached_paths),
                    "misses": run.cache_misses,
                    **({} if scope else {"reason": "engine results are not cacheable per file"}),
                },
            }
            if (
                scope is not None
                and outcome.state in _COMPLETED
                and outcome.engine_version == scope.engine_version
            ):
                shas = {f.path: f.sha256 for f in files}
                fresh_ok = [
                    p for p in outcome.attempted if p not in problems and p not in cached_paths
                ]
                by_path: dict[str, list[NormalizedFinding]] = {p: [] for p in fresh_ok}
                for found in normalized:
                    if found.raw.path in by_path:
                        by_path[found.raw.path].append(found)
                await engine_cache.store(
                    session,
                    scope,
                    {p: (keys[p], shas[p], by_path[p]) for p in fresh_ok if p in keys},
                )
            if scope is not None:
                await engine_cache.touch(session, scope.project_id, (keys[p] for p in cached_paths))
            run.finished_at = datetime.now(UTC)
            await self._event(
                session,
                task.scan_id,
                "engine_finished",
                engine=task.engine,
                state=run.state,
                findings=len(seen),
            )
        return EngineTaskResult(engine=task.engine, state=outcome.state.value, findings=len(seen))

    # -- finalize ------------------------------------------------------------------------------

    @activity.defn(name="scan.finalize")
    async def finalize(self, payload: FinalizeInput) -> ScanWorkflowResult:
        async with transaction(self._sessions) as session:
            scan = await session.get(Scan, payload.scan_id, with_for_update=True)
            if scan is None:
                raise RuntimeError("scan not found")
            if ScanState(scan.state).is_terminal:
                return ScanWorkflowResult(scan_id=scan.id, state=scan.state)
            runs = list(
                (
                    await session.execute(select(EngineRun).where(EngineRun.scan_id == scan.id))
                ).scalars()
            )
            for run in runs:
                if EngineState(run.state) not in TERMINAL_ENGINE:
                    run.state = (
                        EngineState.CANCELED if payload.canceled else EngineState.FAILED
                    ).value
                    run.error_code = "canceled" if payload.canceled else "worker_error"
                    run.error_message = (
                        "Scan canceled"
                        if payload.canceled
                        else "Engine activity failed after retries"
                    )
                    run.finished_at = datetime.now(UTC)
            states = [EngineState(r.state) for r in runs if r.engine not in EXTRACTOR_NAMES]
            applicable = [s for s in states if s is not EngineState.NOT_APPLICABLE]
            if payload.canceled:
                final = ScanState.CANCELED
            elif not applicable or all(s is EngineState.SUCCEEDED for s in applicable):
                final = ScanState.SUCCEEDED
            elif any(s in _COMPLETED for s in applicable):
                final = ScanState.PARTIAL
            else:
                final = ScanState.FAILED
            degraded = {EngineState.PARTIAL, EngineState.FAILED, EngineState.CANCELED}
            if final is ScanState.SUCCEEDED and any(
                EngineState(r.state) in degraded for r in runs if r.engine in EXTRACTOR_NAMES
            ):
                final = ScanState.PARTIAL
            graph_run = next((r for r in runs if r.engine == "graph"), None)
            if graph_run is not None and EngineState(graph_run.state) in {
                EngineState.FAILED,
                EngineState.CANCELED,
            }:
                published = await session.scalar(
                    select(GraphBuild.id).where(GraphBuild.scan_id == scan.id).limit(1)
                )
                if published is None:
                    await graph_job.publish_failed(
                        session,
                        scope={
                            "workspace_id": scan.workspace_id,
                            "project_id": scan.project_id,
                            "snapshot_id": scan.snapshot_id,
                        },
                        scan_id=scan.id,
                        reason=graph_run.error_message or graph_run.state,
                    )
            scan.state = require_scan_transition(ScanState.RUNNING, final).value
            scan.finished_at = datetime.now(UTC)
            lifecycle = await apply_lifecycle(session, scan, final, runs)
            scan.summary = {**await self._summary(session, scan.id, runs), "lifecycle": lifecycle}
            await self._event(session, scan.id, "scan_finished", state=final.value)
            return ScanWorkflowResult(scan_id=scan.id, state=final.value)

    async def _summary(
        self, session: AsyncSession, scan_id: UUID, runs: list[EngineRun]
    ) -> dict[str, object]:
        rows = (
            await session.execute(
                select(Finding.severity, Finding.category, Finding.engine).where(
                    Finding.scan_id == scan_id
                )
            )
        ).all()
        limitations: list[str] = []
        for run in runs:
            state = EngineState(run.state)
            if state is EngineState.UNAVAILABLE:
                limitations.append(f"{run.engine}: not run ({run.error_message})")
            elif state in {EngineState.FAILED, EngineState.CANCELED}:
                limitations.append(f"{run.engine}: {state.value.lower()} ({run.error_code})")
            elif run.files_failed:
                failed, eligible = run.files_failed, run.files_eligible
                limitations.append(f"{run.engine}: {failed} of {eligible} eligible files failed")
        limitations.append(
            "Source-only analysis: no build, type resolution or runtime verification."
        )
        cache = {
            r.engine: {"hits": r.cache_hits, "misses": r.cache_misses}
            for r in runs
            if r.cache_hits or r.cache_misses
        }
        return {
            "cache": cache,
            "findings": len(rows),
            "by_severity": dict(Counter(r[0] for r in rows)),
            "by_category": dict(Counter(r[1] for r in rows)),
            "by_engine": dict(Counter(r[2] for r in rows)),
            "limitations": limitations,
        }

    def all(self) -> list[object]:
        return [self.prepare, self.run_engine, self.finalize]


@workflow.defn(name=SCAN_WORKFLOW_NAME)
class ScanWorkflow:
    @workflow.run
    async def run(self, payload: ScanWorkflowInput) -> ScanWorkflowResult:
        plan = await workflow.execute_activity(
            "scan.prepare",
            payload,
            result_type=ScanPlan,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=5),
        )
        if plan.terminal:
            return ScanWorkflowResult(scan_id=payload.scan_id, state="TERMINAL")
        try:
            await asyncio.gather(
                *(
                    workflow.execute_activity(
                        "scan.engine",
                        EngineTask(scan_id=payload.scan_id, engine=engine),
                        result_type=EngineTaskResult,
                        start_to_close_timeout=timedelta(hours=2),
                        heartbeat_timeout=timedelta(seconds=60),
                        cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                        retry_policy=RetryPolicy(
                            maximum_attempts=2, initial_interval=timedelta(seconds=2)
                        ),
                    )
                    for engine in plan.engines
                ),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            await workflow.execute_activity(
                "scan.finalize",
                FinalizeInput(scan_id=payload.scan_id, canceled=True),
                result_type=ScanWorkflowResult,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=5),
            )
            raise
        final: ScanWorkflowResult = await workflow.execute_activity(
            "scan.finalize",
            FinalizeInput(scan_id=payload.scan_id),
            result_type=ScanWorkflowResult,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=RetryPolicy(maximum_attempts=5),
        )
        return final
