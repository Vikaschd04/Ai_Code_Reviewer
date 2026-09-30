"""Graph extraction and publication for one scan (snapshot-scoped, superseding builds).

Extraction reads stored blobs (never executes code), reuses per-file facts from the syntax cache
(blob hash + extractor version), then resolves references across the whole snapshot. Resolution
is always recomputed because manifests, tsconfig files and other files change its outcome.
Publishing a build marks it current and deletes the nodes/edges of earlier builds for the
snapshot, in one transaction, so earlier links are never served as current facts.
"""

from __future__ import annotations

import hashlib
import posixpath
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, insert, update
from sqlalchemy.ext.asyncio import AsyncSession

from crp_analysis.engines.base import CacheIdentity, CancelToken
from crp_analysis.frameworks import registry as frameworks
from crp_analysis.graph.extract import (
    GRAPH_EXTRACTOR,
    FileFacts,
    extract_facts,
    graph_extractor_version,
)
from crp_analysis.graph.manifests import (
    Module,
    TsConfig,
    parse_package_json,
    parse_pom,
    parse_tsconfig,
)
from crp_analysis.graph.resolve import GraphData, build_graph
from crp_core.artifacts import ArtifactKey, ArtifactStore
from crp_core.db.models import GraphBuild, GraphEdge, GraphNode
from crp_core.domain.states import FileDisposition, GraphBuildState

MANIFEST_NAMES = frozenset({"pom.xml", "package.json", "tsconfig.json"})
_GRADLE = ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")


def graph_cache_identity() -> CacheIdentity:
    digest = hashlib.sha256(GRAPH_EXTRACTOR.encode()).hexdigest()
    return CacheIdentity(digest, digest)


@dataclass(frozen=True, slots=True)
class SnapshotFile:
    entry_id: UUID
    path: str
    sha256: str | None
    language: str | None
    disposition: str


@dataclass(slots=True)
class GraphRun:
    facts: dict[str, FileFacts]
    fresh: dict[str, FileFacts]
    data: GraphData
    notes: list[str] = field(default_factory=list)
    canceled: bool = False


def _blob(store: ArtifactStore, sha: str, max_bytes: int) -> bytes:
    return store.read_bytes(ArtifactKey(f"blobs/{sha[:2]}/{sha}"), max_bytes=max_bytes)


def run_graph(
    store: ArtifactStore,
    sources: list[SnapshotFile],
    snapshot: list[SnapshotFile],
    cached: dict[str, FileFacts],
    *,
    max_file_bytes: int,
    cancel: CancelToken,
) -> GraphRun:
    facts = dict(cached)
    fresh: dict[str, FileFacts] = {}
    for item in sources:
        if cancel.cancelled:
            return GraphRun(facts, fresh, GraphData(), canceled=True)
        if item.path in facts or item.sha256 is None:
            continue
        parsed = extract_facts(_blob(store, item.sha256, max_file_bytes), item.path, item.language)
        facts[item.path] = fresh[item.path] = parsed
    modules: list[Module] = []
    tsconfigs: list[TsConfig] = []
    notes: list[str] = []
    analyzable = [
        s
        for s in snapshot
        if s.disposition == FileDisposition.ANALYZABLE.value and s.sha256 is not None
    ]
    detection = frameworks.detect({s.path for s in analyzable})
    pack_texts: dict[str, str] = {}
    if detection.any:
        for item in analyzable:
            if cancel.cancelled:
                return GraphRun(facts, fresh, GraphData(), canceled=True)
            if len(pack_texts) >= frameworks.MAX_PACK_FILES:
                notes.append("framework mapping stopped at the file limit")
                break
            if item.sha256 is not None and frameworks.wants(item.path, detection):
                data = _blob(store, item.sha256, max_file_bytes)
                pack_texts[item.path] = data.decode("utf-8", errors="replace")
    prepared = frameworks.prepare(pack_texts, detection)
    modules.extend(prepared.modules)
    for item in snapshot:
        name = posixpath.basename(item.path)
        if name in _GRADLE:
            notes.append(f"{item.path}: Gradle build files are not evaluated")
        if (
            name not in MANIFEST_NAMES
            or item.disposition != FileDisposition.ANALYZABLE.value
            or item.sha256 is None
        ):
            continue
        data = _blob(store, item.sha256, max_file_bytes)
        if name == "pom.xml":
            modules.append(parse_pom(item.path, data))
        elif name == "package.json":
            modules.append(parse_package_json(item.path, data))
        else:
            config, note = parse_tsconfig(item.path, data)
            if config is not None:
                tsconfigs.append(config)
            if note:
                notes.append(note)
    graph = build_graph(
        sources={s.path: s.language for s in sources},
        facts=facts,
        known_files={s.path: s.disposition for s in snapshot},
        modules=modules,
        tsconfigs=tsconfigs,
    )
    notes.extend(f"{m.manifest}: {n}" for m in modules for n in m.notes)
    reports = frameworks.map_packs(graph, pack_texts, prepared)
    graph.diagnostics["frameworks"] = [report.to_json() for report in reports]
    return GraphRun(facts, fresh, graph, sorted(set(notes))[:100])


def build_state(sources: list[SnapshotFile], run: GraphRun) -> GraphBuildState:
    statuses = [run.facts[s.path].status for s in sources if s.path in run.facts]
    failed = sum(1 for s in statuses if s == "FAILED")
    if sources and failed == len(sources):
        return GraphBuildState.FAILED
    if failed or any(s == "PARTIAL" for s in statuses) or run.data.truncated:
        return GraphBuildState.PARTIAL
    if len(statuses) < len(sources):
        return GraphBuildState.PARTIAL
    return GraphBuildState.SUCCEEDED


async def supersede(session: AsyncSession, snapshot_id: UUID) -> None:
    """Retire the current build for a snapshot and drop the facts of retired builds."""
    await session.execute(
        update(GraphBuild)
        .where(GraphBuild.snapshot_id == snapshot_id, GraphBuild.is_current.is_(True))
        .values(is_current=False)
    )
    await session.execute(delete(GraphNode).where(GraphNode.snapshot_id == snapshot_id))


async def publish(
    session: AsyncSession,
    *,
    scope: dict[str, UUID],
    scan_id: UUID,
    sources: list[SnapshotFile],
    snapshot: list[SnapshotFile],
    run: GraphRun,
    state: GraphBuildState,
    cache_hits: int,
) -> GraphBuild:
    snapshot_id = scope["snapshot_id"]
    await supersede(session, snapshot_id)
    entry_ids = {s.path: s.entry_id for s in snapshot}
    data = run.data
    failed = sum(
        1 for s in sources if run.facts.get(s.path, FileFacts("FAILED")).status == "FAILED"
    )
    unresolved = sum(1 for e in data.edges if e.classification == "unresolved")
    build = GraphBuild(
        workspace_id=scope["workspace_id"],
        project_id=scope["project_id"],
        snapshot_id=snapshot_id,
        scan_id=scan_id,
        state=state.value,
        is_current=True,
        extractor=graph_extractor_version(),
        node_count=len(data.nodes),
        edge_count=len(data.edges),
        unresolved_count=unresolved,
        files_parsed=len(sources) - failed,
        files_failed=failed,
        diagnostics={
            **data.diagnostics,
            "notes": run.notes,
            "cache_hits": cache_hits,
            "partial_parses": sum(
                1 for s in sources if run.facts.get(s.path, FileFacts("OK")).status == "PARTIAL"
            ),
            "limits": "Syntax-level: no classpath, installed packages, type checker or call "
            "graph. Reflection, dependency injection and dynamic dispatch are not detected.",
        },
        finished_at=datetime.now(UTC),
    )
    session.add(build)
    await session.flush()
    base = {
        "workspace_id": scope["workspace_id"],
        "project_id": scope["project_id"],
        "snapshot_id": snapshot_id,
        "build_id": build.id,
    }
    ids: dict[str, int] = {}
    node_rows = [
        {
            **base,
            "kind": n.kind,
            "key": n.key[:1024],
            "label": n.label[:512],
            "module_key": n.module_key,
            "file_entry_id": entry_ids.get(n.path) if n.path else None,
            "start_line": n.start_line,
            "end_line": n.end_line,
            "attributes": n.attributes or None,
        }
        for n in data.nodes.values()
    ]
    for chunk in range(0, len(node_rows), 5000):
        result = await session.execute(
            insert(GraphNode).returning(GraphNode.id, GraphNode.key),
            node_rows[chunk : chunk + 5000],
        )
        ids.update({key: node_id for node_id, key in result.all()})
    edge_rows = [
        {
            **base,
            "source_node_id": ids[e.source],
            "target_node_id": ids.get(e.target) if e.target else None,
            "relation": e.relation,
            "classification": e.classification,
            "target_ref": e.target_ref,
            "reason": e.reason,
            "evidence_file_entry_id": entry_ids.get(e.path) if e.path else None,
            "evidence_start_line": e.start_line,
            "evidence_end_line": e.end_line,
            "evidence_text": e.text[:500] if e.text else None,
            "extractor": e.extractor or GRAPH_EXTRACTOR,
        }
        for e in data.edges
        if e.source in ids
    ]
    for chunk in range(0, len(edge_rows), 5000):
        await session.execute(insert(GraphEdge), edge_rows[chunk : chunk + 5000])
    return build


async def publish_failed(
    session: AsyncSession, *, scope: dict[str, UUID], scan_id: UUID, reason: str
) -> None:
    """A failed/canceled extraction still becomes current, so no earlier links look current."""
    await supersede(session, scope["snapshot_id"])
    session.add(
        GraphBuild(
            workspace_id=scope["workspace_id"],
            project_id=scope["project_id"],
            snapshot_id=scope["snapshot_id"],
            scan_id=scan_id,
            state=GraphBuildState.FAILED.value,
            is_current=True,
            extractor=graph_extractor_version(),
            diagnostics={"error": reason},
            finished_at=datetime.now(UTC),
        )
    )
