"""Architecture model from a snapshot's graph build (P10 slice 1; ADR 0018).

Reads the build's file and type nodes and its resolved or inferred edges (shared reader, also used
by the worker's architecture rules), and turns them into the inputs of
``crp_analysis.architecture.metrics``. Edges to modules, external packages and unresolved targets
are not counted (their number is reported).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from crp_analysis.architecture.model import ArchitectureModel, build_model
from crp_core.db.graph_reads import architecture_rows
from crp_core.db.models import GraphBuild


def abstract_known(build: GraphBuild) -> bool:
    """Builds from the first extractor did not record abstract types."""
    return "crp-graph-extract-v1" not in build.extractor


async def load_model(session: AsyncSession, build: GraphBuild) -> ArchitectureModel:
    rows = await architecture_rows(session, build.id)
    return build_model(rows.nodes, rows.edges, rows.other_edges)
