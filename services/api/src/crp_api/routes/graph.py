"""Snapshot graph APIs: summary, node search, bounded neighborhood and bounded impact.

Only the snapshot's current build is served. A failed latest build yields no nodes (earlier
links are never presented as current). All queries are limited (depth, node and edge counts)
and report truncation; nothing returns an entire large graph.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import case, func, or_, select, text

from crp_api.auth.dependencies import Container, CurrentPrincipal
from crp_api.errors import ApiError, ErrorResponse
from crp_api.schemas import (
    FrameworkPack,
    GraphBuildResponse,
    GraphEdgeResponse,
    GraphImpact,
    GraphNeighborhood,
    GraphNodePage,
    GraphNodeResponse,
    GraphSummary,
    ImpactItem,
    ModuleDependency,
    ModuleSummary,
)
from crp_api.services.scope import decode_offset, encode_offset, get_scoped
from crp_core.db.models import FileEntry, GraphBuild, GraphEdge, GraphNode, Snapshot
from crp_core.db.session import transaction
from crp_core.domain.states import GraphBuildState

router = APIRouter(tags=["graph"], responses={401: {"model": ErrorResponse}})
_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
}
NEIGHBORHOOD_EDGE_LIMIT = 600
IMPACT_RELATIONS = (
    "imports",
    "reexports",
    "requires",
    "dynamic_import",
    "extends",
    "implements",
    "depends_on",
)
_KIND_RANK = {"module": 0, "file": 1, "type": 2, "package": 3, "external": 4, "function": 5}


def _build(build: GraphBuild) -> GraphBuildResponse:
    return GraphBuildResponse(
        id=build.id,
        snapshot_id=build.snapshot_id,
        scan_id=build.scan_id,
        state=build.state,
        extractor=build.extractor,
        node_count=build.node_count,
        edge_count=build.edge_count,
        unresolved_count=build.unresolved_count,
        files_parsed=build.files_parsed,
        files_failed=build.files_failed,
        diagnostics=build.diagnostics,
        created_at=build.created_at,
    )


def _node(node: GraphNode, path: str | None) -> GraphNodeResponse:
    return GraphNodeResponse(
        id=node.id,
        kind=node.kind,
        key=node.key,
        label=node.label,
        module_key=node.module_key,
        file_id=node.file_entry_id,
        path=path,
        start_line=node.start_line,
        end_line=node.end_line,
        attributes=node.attributes,
    )


def _edge(edge: GraphEdge, path: str | None) -> GraphEdgeResponse:
    return GraphEdgeResponse(
        id=edge.id,
        source_id=edge.source_node_id,
        target_id=edge.target_node_id,
        relation=edge.relation,
        classification=edge.classification,
        target_ref=edge.target_ref,
        reason=edge.reason,
        evidence_file_id=edge.evidence_file_entry_id,
        evidence_path=path,
        evidence_start_line=edge.evidence_start_line,
        evidence_end_line=edge.evidence_end_line,
        evidence_text=edge.evidence_text,
        extractor=edge.extractor,
    )


async def _current(
    session: Any, principal: Any, snapshot_id: uuid.UUID
) -> tuple[Snapshot, GraphBuild | None]:
    snapshot = await get_scoped(
        session, principal, Snapshot, snapshot_id, not_found="snapshot_not_found"
    )
    build = (
        await session.execute(
            select(GraphBuild).where(
                GraphBuild.snapshot_id == snapshot.id, GraphBuild.is_current.is_(True)
            )
        )
    ).scalar_one_or_none()
    return snapshot, build


async def _usable(session: Any, principal: Any, snapshot_id: uuid.UUID) -> GraphBuild:
    _, build = await _current(session, principal, snapshot_id)
    if build is None:
        raise ApiError(404, "graph_not_built", "No graph has been built for this snapshot yet")
    if build.state == GraphBuildState.FAILED.value:
        raise ApiError(409, "graph_build_failed", "The latest graph build failed; rescan")
    return build


async def _nodes(session: Any, ids: set[int]) -> dict[int, GraphNodeResponse]:
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(GraphNode, FileEntry.path)
            .outerjoin(FileEntry, FileEntry.id == GraphNode.file_entry_id)
            .where(GraphNode.id.in_(ids))
        )
    ).all()
    return {n.id: _node(n, path) for n, path in rows}


async def _node_in_build(session: Any, build: GraphBuild, node_id: int) -> GraphNode:
    node: GraphNode | None = await session.get(GraphNode, node_id)
    if node is None or node.build_id != build.id:
        raise ApiError(404, "graph_node_not_found", "Graph node not found in the current build")
    return node


@router.get("/snapshots/{snapshot_id}/graph", response_model=GraphSummary, responses=_ERRORS)
async def graph_summary(
    snapshot_id: uuid.UUID, principal: CurrentPrincipal, container: Container
) -> GraphSummary:
    async with transaction(container.session_factory) as session:
        _, build = await _current(session, principal, snapshot_id)
        empty: dict[str, Any] = {
            "nodes_by_kind": {},
            "edges_by_classification": {},
            "edges_by_relation": {},
            "modules": [],
            "module_dependencies": [],
            "unresolved_reasons": {},
        }
        if build is None:
            return GraphSummary(
                build=None,
                status="none",
                message="No graph has been built yet; run a scan",
                **empty,
            )
        if build.state == GraphBuildState.FAILED.value:
            error = (build.diagnostics or {}).get("error", "unknown error")
            return GraphSummary(
                build=_build(build),
                status="failed",
                message=f"The latest graph build failed ({error}); earlier links are not shown",
                **empty,
            )
        by_kind = dict(
            (
                await session.execute(
                    select(GraphNode.kind, func.count())
                    .where(GraphNode.build_id == build.id)
                    .group_by(GraphNode.kind)
                )
            ).all()
        )
        by_class = dict(
            (
                await session.execute(
                    select(GraphEdge.classification, func.count())
                    .where(GraphEdge.build_id == build.id)
                    .group_by(GraphEdge.classification)
                )
            ).all()
        )
        by_relation = dict(
            (
                await session.execute(
                    select(GraphEdge.relation, func.count())
                    .where(GraphEdge.build_id == build.id)
                    .group_by(GraphEdge.relation)
                )
            ).all()
        )
        modules = (
            await session.execute(
                select(GraphNode, FileEntry.path)
                .outerjoin(FileEntry, FileEntry.id == GraphNode.file_entry_id)
                .where(GraphNode.build_id == build.id, GraphNode.kind == "module")
                .order_by(GraphNode.label)
                .limit(200)
            )
        ).all()
        member_counts = {
            (key, kind): count
            for key, kind, count in (
                await session.execute(
                    select(GraphNode.module_key, GraphNode.kind, func.count())
                    .where(GraphNode.build_id == build.id, GraphNode.module_key.is_not(None))
                    .group_by(GraphNode.module_key, GraphNode.kind)
                )
            ).all()
        }
        deps = (
            await session.execute(
                text(
                    """
                    SELECT coalesce(s.module_key, s.key) AS src,
                           coalesce(t.module_key, t.key) AS dst,
                           CASE WHEN e.relation = 'depends_on' THEN 'depends_on'
                                WHEN e.extractor LIKE 'crp-pack-%' THEN 'config'
                                ELSE 'code' END AS relation,
                           count(*) AS edges,
                           CASE WHEN bool_and(e.classification = 'resolved') THEN 'resolved'
                                ELSE 'mixed' END AS classification
                    FROM graph_edges e
                    JOIN graph_nodes s ON s.id = e.source_node_id
                    JOIN graph_nodes t ON t.id = e.target_node_id
                    WHERE e.build_id = :build
                      AND t.kind IN ('file', 'type', 'module', 'component')
                      AND coalesce(s.module_key, s.key) <> coalesce(t.module_key, t.key)
                    GROUP BY 1, 2, 3
                    ORDER BY edges DESC
                    LIMIT 500
                    """
                ),
                {"build": build.id},
            )
        ).all()
    diagnostics = build.diagnostics or {}
    reasons = diagnostics.get("unresolved_reasons")
    packs = diagnostics.get("frameworks")
    frameworks = (
        [FrameworkPack.model_validate(p) for p in packs if isinstance(p, dict)]
        if isinstance(packs, list)
        else []
    )
    partial = build.state == GraphBuildState.PARTIAL.value
    return GraphSummary(
        build=_build(build),
        status="current",
        message=(
            "Current graph (partial: some files had syntax errors or limits were reached)"
            if partial
            else "Current graph"
        ),
        nodes_by_kind={str(k): int(v) for k, v in by_kind.items()},
        edges_by_classification={str(k): int(v) for k, v in by_class.items()},
        edges_by_relation={str(k): int(v) for k, v in by_relation.items()},
        modules=[
            ModuleSummary(
                node=_node(n, path),
                files=member_counts.get((n.key, "file"), 0),
                types=member_counts.get((n.key, "type"), 0),
            )
            for n, path in modules
        ],
        module_dependencies=[
            ModuleDependency(
                source_key=r.src,
                target_key=r.dst,
                relation=r.relation,
                edges=int(r.edges),
                classification=r.classification,
            )
            for r in deps
        ],
        unresolved_reasons={str(k): int(v) for k, v in reasons.items()}
        if isinstance(reasons, dict)
        else {},
        frameworks=frameworks,
    )


@router.get(
    "/snapshots/{snapshot_id}/graph/nodes",
    response_model=GraphNodePage,
    responses={**_ERRORS, 409: {"model": ErrorResponse}},
)
async def search_nodes(
    snapshot_id: uuid.UUID,
    principal: CurrentPrincipal,
    container: Container,
    q: Annotated[str | None, Query(max_length=200)] = None,
    kind: Annotated[str | None, Query(max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
) -> GraphNodePage:
    offset = decode_offset(cursor)
    async with transaction(container.session_factory) as session:
        build = await _usable(session, principal, snapshot_id)
        query = (
            select(GraphNode, FileEntry.path)
            .outerjoin(FileEntry, FileEntry.id == GraphNode.file_entry_id)
            .where(GraphNode.build_id == build.id)
        )
        if kind:
            query = query.where(GraphNode.kind == kind)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(
                or_(
                    GraphNode.label.ilike(f"%{escaped}%", escape="\\"),
                    GraphNode.key.ilike(f"%{escaped}%", escape="\\"),
                )
            )
        total = (
            await session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rank = case(_KIND_RANK, value=GraphNode.kind, else_=9)
        rows = (
            await session.execute(
                query.order_by(rank, func.length(GraphNode.label), GraphNode.label, GraphNode.id)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
    return GraphNodePage(
        items=[_node(n, path) for n, path in rows[:limit]],
        next_cursor=encode_offset(offset + limit) if len(rows) > limit else None,
        total=total,
    )


@router.get(
    "/snapshots/{snapshot_id}/graph/nodes/{node_id}/neighborhood",
    response_model=GraphNeighborhood,
    responses={**_ERRORS, 409: {"model": ErrorResponse}},
)
async def node_neighborhood(
    snapshot_id: uuid.UUID,
    node_id: int,
    principal: CurrentPrincipal,
    container: Container,
    depth: Annotated[int, Query(ge=1, le=2)] = 1,
    limit: Annotated[int, Query(ge=5, le=300)] = 80,
) -> GraphNeighborhood:
    """Nodes and edges within ``depth`` hops (in either direction), bounded by ``limit`` nodes."""
    async with transaction(container.session_factory) as session:
        build = await _usable(session, principal, snapshot_id)
        center = await _node_in_build(session, build, node_id)
        seen: set[int] = {center.id}
        frontier = {center.id}
        edges: dict[int, GraphEdge] = {}
        truncated = False
        for _ in range(depth):
            if not frontier:
                break
            batch = list(
                (
                    await session.execute(
                        select(GraphEdge)
                        .where(
                            GraphEdge.build_id == build.id,
                            or_(
                                GraphEdge.source_node_id.in_(frontier),
                                GraphEdge.target_node_id.in_(frontier),
                            ),
                        )
                        .order_by(GraphEdge.id)
                        .limit(NEIGHBORHOOD_EDGE_LIMIT + 1)
                    )
                ).scalars()
            )
            if len(batch) > NEIGHBORHOOD_EDGE_LIMIT:
                truncated = True
                batch = batch[:NEIGHBORHOOD_EDGE_LIMIT]
            next_frontier: set[int] = set()
            for edge in batch:
                ends = [edge.source_node_id] + (
                    [edge.target_node_id] if edge.target_node_id is not None else []
                )
                new = [n for n in ends if n not in seen]
                if len(seen) + len(new) > limit:
                    truncated = True
                    continue
                seen.update(new)
                next_frontier.update(new)
                edges[edge.id] = edge
            frontier = next_frontier
        nodes = await _nodes(session, seen)
        paths = dict(
            (
                await session.execute(
                    select(FileEntry.id, FileEntry.path).where(
                        FileEntry.id.in_(
                            {e.evidence_file_entry_id for e in edges.values()} - {None}
                        )
                    )
                )
            ).all()
        )
    return GraphNeighborhood(
        build_id=build.id,
        center=nodes[center.id],
        depth=depth,
        nodes=sorted(nodes.values(), key=lambda n: (_KIND_RANK.get(n.kind, 9), n.label)),
        edges=[
            _edge(e, paths.get(e.evidence_file_entry_id) if e.evidence_file_entry_id else None)
            for e in edges.values()
        ],
        truncated=truncated,
    )


# One breadth-first level: file/module units whose relations target any node of the frontier
# units (a file unit also stands for the types it declares). Visited units are never expanded
# twice, so the cost is bounded by the dependents found, not by the number of paths.
_IMPACT_LEVEL_SQL = text(
    """
    WITH targets AS (
        SELECT n.id FROM graph_nodes n
        WHERE n.build_id = :build
          AND (n.id = ANY(:frontier)
               OR (n.kind = 'type' AND n.file_entry_id IN (
                   SELECT f.file_entry_id FROM graph_nodes f
                   WHERE f.build_id = :build AND f.kind = 'file' AND f.id = ANY(:frontier))))
    )
    SELECT src.id AS unit, min(e.relation) AS via
    FROM graph_edges e
    JOIN graph_nodes src
      ON src.build_id = e.build_id AND src.kind IN ('file', 'module')
     AND src.file_entry_id = e.evidence_file_entry_id
    WHERE e.build_id = :build
      AND e.target_node_id IN (SELECT id FROM targets)
      AND e.classification IN ('resolved', 'declared', 'inferred')
      AND e.relation = ANY(:relations)
      AND NOT (src.id = ANY(:visited))
    GROUP BY src.id
    ORDER BY src.id
    """
)


@router.get(
    "/snapshots/{snapshot_id}/graph/nodes/{node_id}/impact",
    response_model=GraphImpact,
    responses={**_ERRORS, 409: {"model": ErrorResponse}},
)
async def node_impact(
    snapshot_id: uuid.UUID,
    node_id: int,
    principal: CurrentPrincipal,
    container: Container,
    depth: Annotated[int, Query(ge=1, le=5)] = 3,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> GraphImpact:
    """Files/modules that (transitively) depend on the node, computed at file level."""
    async with transaction(container.session_factory) as session:
        build = await _usable(session, principal, snapshot_id)
        target = await _node_in_build(session, build, node_id)
        start = target
        if target.kind == "type" and target.file_entry_id is not None:
            start = (
                await session.execute(
                    select(GraphNode).where(
                        GraphNode.build_id == build.id,
                        GraphNode.kind == "file",
                        GraphNode.file_entry_id == target.file_entry_id,
                    )
                )
            ).scalar_one_or_none() or target
        found: list[tuple[int, int, str]] = []  # (unit, depth, via)
        visited = {start.id}
        frontier = [start.id]
        truncated = False
        for level in range(1, depth + 1):
            if not frontier:
                break
            batch = (
                await session.execute(
                    _IMPACT_LEVEL_SQL,
                    {
                        "build": build.id,
                        "frontier": frontier,
                        "visited": list(visited),
                        "relations": list(IMPACT_RELATIONS),
                    },
                )
            ).all()
            frontier = []
            for row in batch:
                if len(found) >= limit:
                    truncated = True
                    break
                visited.add(row.unit)
                frontier.append(row.unit)
                found.append((row.unit, level, str(row.via)))
            if truncated:
                break
        nodes = await _nodes(session, {unit for unit, _, _ in found} | {target.id})
    diagnostics = build.diagnostics or {}
    partial_parses = diagnostics.get("partial_parses", 0)
    problems = build.files_failed + (partial_parses if isinstance(partial_parses, int) else 0)
    caveats = [
        "Syntax-level relations only: reflection, dependency injection, dynamic dispatch and "
        "runtime configuration are not detected.",
        "Computed at file/module level"
        + (" from the type's declaring file." if start is not target else "."),
    ]
    if build.unresolved_count:
        caveats.append(
            f"{build.unresolved_count} unresolved edge(s) in this build may hide further "
            "dependents."
        )
    if problems:
        caveats.append(f"{problems} file(s) had parse errors; their relations may be incomplete.")
    return GraphImpact(
        build_id=build.id,
        target=nodes[target.id],
        max_depth=depth,
        dependents=[
            ImpactItem(node=nodes[unit], depth=level, via=via)
            for unit, level, via in found
            if unit in nodes
        ],
        truncated=truncated,
        unresolved_edges_in_build=build.unresolved_count,
        files_with_parse_problems=problems,
        caveats=caveats,
    )
