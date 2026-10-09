"""Structural architecture metrics (P10 slice 1; ADR 0018) computed from the snapshot graph.

Components are Java packages (from the types a file declares) and, for JavaScript/TypeScript and
everything else, the folder a file is in. Test code is left out of the model and counted.

Per component (R. C. Martin, "Agile Software Development", 2002, ch. 20), with files standing in
for classes:
- Ca (afferent coupling): files outside the component that depend on files inside it;
- Ce (efferent coupling): files inside the component that depend on files outside it;
- instability I = Ce / (Ca + Ce), undefined without dependencies;
- abstractness A = abstract types (interfaces, abstract classes) / all types, undefined without
  types;
- distance from the main sequence D = |A + I - 1|. D >= 0.7 is the "zone of pain" (concrete and
  stable, A + I < 1) or the "zone of uselessness" (abstract and unstable, A + I > 1). A zone is
  only reported with enough evidence: pain needs at least 3 dependent files (a leaf used by one
  file is not hard to change) and uselessness at least 2 abstract types.

Cycles are strongly connected components (Tarjan) of the component graph. For each, the cheapest
set of dependencies to cut (fewest file-level dependencies) is found exactly for small cycles (up to
16 component dependencies). Larger ones use the weighted Eades-Lin-Smyth heuristic, then put back
any cut dependency that does not close a cycle; these are marked approximate.

Only resolved and inferred graph edges count. Undefined values stay None, never zero.
"""

from __future__ import annotations

import itertools
import posixpath
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

ALGORITHM = "crp-architecture-metrics-v1"
ZONE_DISTANCE = 0.7
PAIN_MIN_DEPENDENTS = 3
USELESS_MIN_ABSTRACT = 2
EXACT_CUT_EDGE_LIMIT = 16
CLEANUP_EDGE_LIMIT = 4000  # restoring cut edges costs O(cut x E)


@dataclass(frozen=True, slots=True)
class CodeFile:
    path: str
    lines: int | None
    package: str | None = None  # Java package of the file's types
    test: bool = False


@dataclass(frozen=True, slots=True)
class CodeType:
    path: str
    abstract: bool


@dataclass(frozen=True, slots=True)
class Dependency:
    source_path: str
    target_path: str | None = None  # a file in the snapshot
    target_package: str | None = None  # an on-demand import of a whole Java package
    line: int | None = None  # evidence line in the source file (import, extends, ...)


@dataclass(slots=True)
class ComponentMetrics:
    key: str
    kind: str  # package | folder
    files: int
    lines: int
    types: int
    abstract_types: int
    afferent: int  # Ca
    efferent: int  # Ce
    fan_in: int  # components that depend on this one
    fan_out: int  # components this one depends on
    instability: float | None
    abstractness: float | None
    distance: float | None
    zone: str | None  # pain | uselessness
    in_cycle: bool = False


@dataclass(frozen=True, slots=True)
class ComponentEdge:
    source: str
    target: str
    weight: int  # distinct file-level dependencies


@dataclass(slots=True)
class Cycle:
    components: list[str]
    edges: list[ComponentEdge]
    cut: list[ComponentEdge]
    exact: bool


@dataclass(slots=True)
class ArchitectureMetrics:
    components: list[ComponentMetrics] = field(default_factory=list)
    edges: list[ComponentEdge] = field(default_factory=list)
    cycles: list[Cycle] = field(default_factory=list)
    test_files: int = 0
    dependencies_counted: int = 0


def component_of(file: CodeFile) -> tuple[str, str]:
    """(component key, kind) of a file."""
    if file.package:
        return file.package, "package"
    return posixpath.dirname(file.path) or ".", "folder"


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def compute(
    files: Iterable[CodeFile],
    types: Iterable[CodeType],
    dependencies: Iterable[Dependency],
    *,
    exact_limit: int = EXACT_CUT_EDGE_LIMIT,
    abstract_known: bool = True,
) -> ArchitectureMetrics:
    """``abstract_known`` is False for graphs that did not record abstract types: abstractness
    and distance are then undefined rather than zero."""
    result = ArchitectureMetrics()
    component: dict[str, str] = {}
    kinds: dict[str, str] = {}
    members: dict[str, list[CodeFile]] = defaultdict(list)
    for file in files:
        if file.test:
            result.test_files += 1
            continue
        key, kind = component_of(file)
        component[file.path] = key
        kinds.setdefault(key, kind)
        members[key].append(file)
    type_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for declared in types:
        owner = component.get(declared.path)
        if owner is None:
            continue
        type_counts[owner][0] += 1
        type_counts[owner][1] += int(declared.abstract)

    # File-level dependencies between different components (duplicates collapse).
    pairs: set[tuple[str, str]] = set()  # (source file, target file or "pkg:<name>")
    for dep in dependencies:
        source = component.get(dep.source_path)
        if source is None:
            continue
        if dep.target_path is not None:
            target = component.get(dep.target_path)
            node = dep.target_path
        elif dep.target_package is not None and dep.target_package in members:
            target = dep.target_package
            node = f"pkg:{dep.target_package}"
        else:
            continue
        if target is None or target == source:
            continue
        pairs.add((dep.source_path, node))
    result.dependencies_counted = len(pairs)

    weights: dict[tuple[str, str], int] = defaultdict(int)
    afferent: dict[str, set[str]] = defaultdict(set)
    efferent: dict[str, set[str]] = defaultdict(set)
    for source_file, node in pairs:
        source = component[source_file]
        target = node[4:] if node.startswith("pkg:") else component[node]
        weights[(source, target)] += 1
        afferent[target].add(source_file)
        efferent[source].add(source_file)
    result.edges = sorted(
        (ComponentEdge(s, t, w) for (s, t), w in weights.items()),
        key=lambda e: (-e.weight, e.source, e.target),
    )
    fan_in: dict[str, int] = defaultdict(int)
    fan_out: dict[str, int] = defaultdict(int)
    for source, target in weights:
        fan_out[source] += 1
        fan_in[target] += 1

    result.cycles = _cycles(sorted(members), weights, exact_limit)
    cyclic = {c for cycle in result.cycles for c in cycle.components}
    for key in sorted(members):
        ca, ce = len(afferent[key]), len(efferent[key])
        total, abstract = type_counts[key]
        instability = ce / (ca + ce) if ca + ce else None
        abstractness = abstract / total if total and abstract_known else None
        distance = (
            abs(abstractness + instability - 1)
            if instability is not None and abstractness is not None
            else None
        )
        zone = None
        if (
            distance is not None
            and abstractness is not None
            and instability is not None
            and distance >= ZONE_DISTANCE
        ):
            if abstractness + instability < 1:
                zone = "pain" if ca >= PAIN_MIN_DEPENDENTS else None
            else:
                zone = "uselessness" if abstract >= USELESS_MIN_ABSTRACT else None
        result.components.append(
            ComponentMetrics(
                key=key,
                kind=kinds[key],
                files=len(members[key]),
                lines=sum(f.lines or 0 for f in members[key]),
                types=total,
                abstract_types=abstract,
                afferent=ca,
                efferent=ce,
                fan_in=fan_in[key],
                fan_out=fan_out[key],
                instability=_round(instability),
                abstractness=_round(abstractness),
                distance=_round(distance),
                zone=zone,
                in_cycle=key in cyclic,
            )
        )
    return result


# -- cycles -------------------------------------------------------------------------------------


def _strongly_connected(nodes: list[str], edges: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan's algorithm (iterative); components with more than one node, sorted."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    found: list[list[str]] = []
    counter = 0
    for start in nodes:
        if start in index:
            continue
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            node, position = work[-1]
            if position == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            targets = edges.get(node, [])
            if position < len(targets):
                work[-1] = (node, position + 1)
                target = targets[position]
                if target not in index:
                    work.append((target, 0))
                elif target in on_stack:
                    low[node] = min(low[node], index[target])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                group: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    group.append(member)
                    if member == node:
                        break
                if len(group) > 1:
                    found.append(sorted(group))
    return sorted(found)


def _acyclic(nodes: list[str], edges: list[ComponentEdge]) -> bool:
    incoming = {n: 0 for n in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        incoming[edge.target] += 1
        outgoing[edge.source].append(edge.target)
    ready = [n for n, count in incoming.items() if count == 0]
    seen = 0
    while ready:
        node = ready.pop()
        seen += 1
        for target in outgoing[node]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    return seen == len(nodes)


def _cheapest_cut(
    nodes: list[str], edges: list[ComponentEdge], exact_limit: int
) -> tuple[list[ComponentEdge], bool]:
    """Fewest file-level dependencies whose removal breaks every cycle among ``nodes``."""
    if len(edges) <= exact_limit:
        best: tuple[int, int, list[ComponentEdge]] | None = None
        for size in range(1, len(edges) + 1):
            for chosen in itertools.combinations(edges, size):
                cost = sum(e.weight for e in chosen)
                if best is not None and (cost, size) >= (best[0], best[1]):
                    continue
                kept = [e for e in edges if e not in chosen]
                if _acyclic(nodes, kept):
                    best = (cost, size, list(chosen))
            if best is not None and best[0] <= size:  # no larger set can be cheaper
                break
        chosen_edges = best[2] if best is not None else list(edges)  # all edges: always acyclic
        return sorted(chosen_edges, key=lambda e: (e.source, e.target)), True
    cut = _eades_lin_smyth(nodes, edges)
    if len(edges) <= CLEANUP_EDGE_LIMIT:
        cut = _restore(edges, cut)
    return sorted(cut, key=lambda e: (e.source, e.target)), False


def _eades_lin_smyth(nodes: list[str], edges: list[ComponentEdge]) -> list[ComponentEdge]:
    """Weighted Eades-Lin-Smyth ordering (1993): peel sinks and sources, otherwise take the node
    with the largest (outgoing - incoming) weight; edges pointing backwards in the order are cut.
    O(V^2 + E): usable on large tangles where the exact search is not."""
    succ: dict[str, dict[str, int]] = defaultdict(dict)
    pred: dict[str, dict[str, int]] = defaultdict(dict)
    for edge in edges:
        succ[edge.source][edge.target] = edge.weight
        pred[edge.target][edge.source] = edge.weight
    remaining = set(nodes)
    out_count = {n: len(succ[n]) for n in nodes}
    in_count = {n: len(pred[n]) for n in nodes}
    out_weight = {n: sum(succ[n].values()) for n in nodes}
    in_weight = {n: sum(pred[n].values()) for n in nodes}
    head: list[str] = []
    tail: list[str] = []

    def remove(node: str) -> None:
        remaining.discard(node)
        for target, weight in succ[node].items():
            if target in remaining:
                in_count[target] -= 1
                in_weight[target] -= weight
        for source, weight in pred[node].items():
            if source in remaining:
                out_count[source] -= 1
                out_weight[source] -= weight

    while remaining:
        progressed = True
        while progressed and remaining:
            progressed = False
            for node in sorted(n for n in remaining if out_count[n] == 0):
                tail.append(node)
                remove(node)
                progressed = True
            for node in sorted(n for n in remaining if in_count[n] == 0):
                head.append(node)
                remove(node)
                progressed = True
        if remaining:
            chosen = max(sorted(remaining), key=lambda n: out_weight[n] - in_weight[n])
            head.append(chosen)
            remove(chosen)
    order = {node: index for index, node in enumerate([*head, *reversed(tail)])}
    return [e for e in edges if order[e.source] > order[e.target]]


def _reaches(edges: list[ComponentEdge], start: str, goal: str) -> bool:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adjacency[edge.source].append(edge.target)
    seen, stack = {start}, [start]
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        for target in adjacency[node]:
            if target not in seen:
                seen.add(target)
                stack.append(target)
    return False


def _restore(edges: list[ComponentEdge], cut: list[ComponentEdge]) -> list[ComponentEdge]:
    """Put back cut edges (heaviest first) that do not close a cycle: a smaller, still valid cut."""
    chosen = set(cut)
    kept = [e for e in edges if e not in chosen]
    for edge in sorted(cut, key=lambda e: (-e.weight, e.source, e.target)):
        if not _reaches(kept, edge.target, edge.source):
            kept.append(edge)
            chosen.discard(edge)
    return [e for e in cut if e in chosen]


def _cycles(nodes: list[str], weights: dict[tuple[str, str], int], exact_limit: int) -> list[Cycle]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for source, target in sorted(weights):
        adjacency[source].append(target)
    cycles = []
    for group in _strongly_connected(nodes, adjacency):
        members = set(group)
        edges = [
            ComponentEdge(s, t, w)
            for (s, t), w in sorted(weights.items())
            if s in members and t in members
        ]
        cut, exact = _cheapest_cut(group, edges, exact_limit)
        cycles.append(Cycle(group, edges, cut, exact))
    return cycles
