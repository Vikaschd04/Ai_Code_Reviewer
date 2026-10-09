"""Read one graph build as plain rows for the architecture model (P10; ADR 0018, ADR 0019).

Shared by the API (Structure health, rule previews) and the worker (architecture rules on every
review), so both see the same files, types and dependencies. Interpretation lives in
``crp_analysis.architecture.model``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crp_core.db.models import FileEntry, GraphEdge, GraphNode
from crp_core.domain.states import EdgeClassification, GraphNodeKind

COUNTED = (EdgeClassification.RESOLVED.value, EdgeClassification.INFERRED.value)


@dataclass(frozen=True, slots=True)
class GraphRows:
    nodes: list[tuple[str, str, str, dict[str, object] | None, int | None]]
    """(key, kind, label, attributes, line count) of file, type and package nodes."""
    edges: list[tuple[str, str, int | None]]
    """(source key, target key, evidence line) of resolved and inferred edges with a target."""
    other_edges: int
    """Edges not counted: unresolved, external or without a target node."""


async def architecture_rows(session: AsyncSession, build_id: uuid.UUID) -> GraphRows:
    rows = (
        await session.execute(
            select(
                GraphNode.id,
                GraphNode.key,
                GraphNode.kind,
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
    keys = {node_id: key for node_id, key, *_ in rows}
    edge_rows = (
        await session.execute(
            select(
                GraphEdge.source_node_id, GraphEdge.target_node_id, GraphEdge.evidence_start_line
            ).where(
                GraphEdge.build_id == build_id,
                GraphEdge.classification.in_(COUNTED),
                GraphEdge.target_node_id.is_not(None),
            )
        )
    ).all()
    other = await session.scalar(
        select(func.count()).where(
            GraphEdge.build_id == build_id,
            GraphEdge.classification.not_in(COUNTED) | GraphEdge.target_node_id.is_(None),
        )
    )
    return GraphRows(
        nodes=[(key, kind, label, attrs, lines) for _, key, kind, label, attrs, lines in rows],
        edges=[
            (keys.get(source, ""), keys.get(target or 0, ""), line)
            for source, target, line in edge_rows
        ],
        other_edges=int(other or 0),
    )
