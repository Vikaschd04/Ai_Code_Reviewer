"""Architecture model from a snapshot's graph build (P10 slice 1; ADR 0018).

Reads the build's file and type nodes and its resolved or inferred edges, and turns them into the
inputs of ``crp_analysis.architecture.metrics``. Edges to modules, external packages and unresolved
targets are not counted (their number is reported).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crp_analysis import policy as scope_policy
from crp_analysis.architecture.metrics import CodeFile, CodeType, Dependency
from crp_core.db.models import FileEntry, GraphBuild, GraphEdge, GraphNode
from crp_core.domain.states import EdgeClassification, GraphNodeKind

COUNTED = (EdgeClassification.RESOLVED.value, EdgeClassification.INFERRED.value)


def _path_of(key: str) -> str | None:
    if key.startswith("file:"):
        return key[5:]
    if key.startswith("type:"):
        return key[5:].split("#", 1)[0]
    return None


def abstract_known(build: GraphBuild) -> bool:
    """Builds from the first extractor did not record abstract types."""
    return "crp-graph-extract-v1" not in build.extractor


async def load_model(
    session: AsyncSession, build: GraphBuild
) -> tuple[list[CodeFile], list[CodeType], list[Dependency], int]:
    """(files, types, dependencies, edges not counted) of one graph build."""
    build_id: uuid.UUID = build.id
    rows = (
        await session.execute(
            select(
                GraphNode.id,
                GraphNode.kind,
                GraphNode.key,
                GraphNode.label,
                GraphNode.attributes,
                FileEntry.line_count,
            )
            .outerjoin(FileEntry, FileEntry.id == GraphNode.file_entry_id)
            .where(
                GraphNode.build_id == build_id,
                GraphNode.kind.in_(
                    [
                        GraphNodeKind.FILE.value,
                        GraphNodeKind.TYPE.value,
                        GraphNodeKind.PACKAGE.value,
                    ]
                ),
            )
        )
    ).all()
    keys: dict[int, str] = {}
    lines: dict[str, int | None] = {}
    packages: dict[str, str] = {}
    types: list[CodeType] = []
    for node_id, kind, key, label, attributes, line_count in rows:
        keys[node_id] = key
        if kind == GraphNodeKind.FILE.value:
            lines[key[5:]] = line_count
        elif kind == GraphNodeKind.TYPE.value:
            path = _path_of(key)
            if path is None:
                continue
            attrs = attributes or {}
            types.append(CodeType(path, attrs.get("abstract") is True))
            fqn = attrs.get("fqn")
            if isinstance(fqn, str) and fqn.endswith(f".{label}"):
                packages.setdefault(path, fqn[: -len(label) - 1])
    files = [
        CodeFile(
            path,
            count,
            packages.get(path),
            scope_policy.classify(path).category == "test",
        )
        for path, count in sorted(lines.items())
    ]
    dependencies: list[Dependency] = []
    edge_rows = (
        await session.execute(
            select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
                GraphEdge.build_id == build_id,
                GraphEdge.classification.in_(COUNTED),
                GraphEdge.target_node_id.is_not(None),
            )
        )
    ).all()
    not_counted = 0
    for source_id, target_id in edge_rows:
        source = _path_of(keys.get(source_id, ""))
        target_key = keys.get(target_id or 0, "")
        if source is None:
            not_counted += 1
            continue
        if target_key.startswith("package:"):
            dependencies.append(Dependency(source, target_package=target_key[8:]))
            continue
        target = _path_of(target_key)
        if target is None:
            not_counted += 1
            continue
        dependencies.append(Dependency(source, target))
    other = await session.scalar(
        select(func.count()).where(
            GraphEdge.build_id == build_id,
            GraphEdge.classification.not_in(COUNTED) | GraphEdge.target_node_id.is_(None),
        )
    )
    return files, types, dependencies, not_counted + int(other or 0)
