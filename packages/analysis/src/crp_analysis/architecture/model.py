"""The architecture model of one graph build: files, types and file-level dependencies (P10).

Built from plain graph rows (``crp_core.db.graph_reads``) so that Structure health, the
architecture rules and the smells read exactly the same model. Test code (scope policy) and
generated code are marked and left out by all of them. Edges to modules, external packages or
unresolved targets are counted, not modelled.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from crp_analysis import policy as scope_policy
from crp_analysis.architecture.metrics import CodeFile, CodeType, Dependency
from crp_analysis.graph.resolve import GraphData
from crp_core.domain.states import EdgeClassification, GraphNodeKind


@dataclass(slots=True)
class ArchitectureModel:
    files: list[CodeFile] = field(default_factory=list)
    types: list[CodeType] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    not_counted: int = 0


GENERATED_DIRECTORIES = frozenset(
    {"gensrc", "generated", "generated-sources", "generated-test-sources", "__generated__"}
)


def is_generated(path: str) -> bool:
    """Generated sources by location (SAP Commerce ``gensrc``, Maven ``generated-sources``,
    ``__generated__``) or name (``*.generated.*``): not part of the team's design."""
    parts = path.split("/")
    return any(part in GENERATED_DIRECTORIES for part in parts[:-1]) or ".generated." in parts[-1]


def path_of(key: str) -> str | None:
    """The file a ``file:`` or ``type:`` node key belongs to."""
    if key.startswith("file:"):
        return key[5:]
    if key.startswith("type:"):
        return key[5:].split("#", 1)[0]
    return None


def build_model(
    nodes: Iterable[tuple[str, str, str, dict[str, object] | None, int | None]],
    edges: Iterable[tuple[str, str, int | None]],
    other_edges: int = 0,
) -> ArchitectureModel:
    """``nodes``: (key, kind, label, attributes, line count); ``edges``: (source key, target
    key, evidence line) of resolved and inferred edges."""
    model = ArchitectureModel()
    lines: dict[str, int | None] = {}
    packages: dict[str, str] = {}
    for key, kind, label, attributes, line_count in nodes:
        if kind == GraphNodeKind.FILE.value:
            lines[key[5:]] = line_count
        elif kind == GraphNodeKind.TYPE.value:
            path = path_of(key)
            if path is None:
                continue
            attrs = attributes or {}
            model.types.append(CodeType(path, attrs.get("abstract") is True))
            fqn = attrs.get("fqn")
            if isinstance(fqn, str) and fqn.endswith(f".{label}"):
                packages.setdefault(path, fqn[: -len(label) - 1])
    model.files = [
        CodeFile(
            path,
            count,
            packages.get(path),
            scope_policy.classify(path).category == "test",
            is_generated(path),
        )
        for path, count in sorted(lines.items())
    ]
    not_counted = other_edges
    for source_key, target_key, line in edges:
        source = path_of(source_key)
        if source is None:
            not_counted += 1
            continue
        if target_key.startswith("package:"):
            model.dependencies.append(Dependency(source, target_package=target_key[8:], line=line))
            continue
        target = path_of(target_key)
        if target is None:
            not_counted += 1
            continue
        model.dependencies.append(Dependency(source, target, line=line))
    model.not_counted = not_counted
    return model


COUNTED = (EdgeClassification.RESOLVED.value, EdgeClassification.INFERRED.value)


def model_from_graph(graph: GraphData, line_counts: dict[str, int | None]) -> ArchitectureModel:
    """The model of an in-memory dependency map (evaluation and tests), read exactly like the
    stored one: file, type and package nodes; resolved and inferred edges with a target."""
    kinds = {GraphNodeKind.FILE.value, GraphNodeKind.TYPE.value, GraphNodeKind.PACKAGE.value}
    nodes = [
        (node.key, node.kind, node.label, node.attributes, line_counts.get(node.path or ""))
        for node in graph.nodes.values()
        if node.kind in kinds
    ]
    edges: list[tuple[str, str, int | None]] = []
    other = 0
    for edge in graph.edges:
        if edge.classification in COUNTED and edge.target is not None:
            edges.append((edge.source, edge.target, edge.start_line))
        else:
            other += 1
    return build_model(nodes, edges, other)
