"""Structural architecture smells (P10 slice 3; ADR 0020), evidence class "potential".

Computed on the architecture model (parts are Java packages and folders; test and generated code
left out) from the upload's dependency map; nothing is guessed and nothing runs.

- **Cyclic dependency** (R. C. Martin's Acyclic Dependencies Principle; Arcan "Cyclic
  Dependency"): parts that depend on each other in a circle, i.e. a strongly connected group of
  the part graph, with the cheapest cut from Structure health (ADR 0018).
- **Unstable dependency** (Martin's Stable Dependencies Principle, "depend in the direction of
  stability"; Arcan "Unstable Dependency"): part A uses part B whose instability exceeds A's by
  more than ``UNSTABLE_DELTA``. A is reported when such parts are at least ``UNSTABLE_SHARE`` of
  the parts it uses. Dependencies inside a cycle are left to the cycle smell (parts in a cycle
  can only change together).
- **Hub-like dependency** (Arcan "Hub-Like Dependency"): a part used by many parts and using many
  parts. Fan-in and fan-out (distinct parts) are both at least the system's upper quartile and at
  least ``HUB_MIN_SIDE``, in a system of at least ``HUB_MIN_PARTS`` parts.

Every smell is one finding per part (so a tangled codebase yields one issue per part, not per
file), anchored at the part's first file with the evidence and that file's first such line;
``anchor_key`` lets the tracked issue follow the part when the anchor file changes.

The thresholds are refactorX's, chosen to need clear evidence, and measured on the labelled set
in ``fixtures/architecture-eval`` (crp-dev arch-eval). They are not claimed to equal Arcan's,
which combine the system with a benchmark of other systems.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from crp_analysis.architecture.metrics import (
    EXACT_CUT_EDGE_LIMIT,
    ArchitectureMetrics,
    CodeFile,
    CodeType,
    Dependency,
    FileDependency,
    compute,
    file_dependencies,
)

ALGORITHM = "crp-architecture-smells-v1"
CYCLE = "crp.arch.cycle"
UNSTABLE = "crp.arch.unstable-dependency"
HUB = "crp.arch.hub"
RULES = (CYCLE, UNSTABLE, HUB)
UNSTABLE_DELTA = 0.1
UNSTABLE_SHARE = 0.3
HUB_MIN_SIDE = 3
HUB_MIN_PARTS = 8
LIST_LIMIT = 5


def thresholds() -> dict[str, object]:
    return {
        "algorithm": ALGORITHM,
        "unstable_delta": UNSTABLE_DELTA,
        "unstable_share": UNSTABLE_SHARE,
        "hub_min_side": HUB_MIN_SIDE,
        "hub_min_parts": HUB_MIN_PARTS,
    }


@dataclass(frozen=True, slots=True)
class Smell:
    rule_id: str
    path: str
    line: int | None
    component: str
    identity: str  # stable fingerprint text: the smell at this file, independent of the line
    title: str
    message: str
    details: dict[str, object]
    anchor_key: str | None = None  # part-level smells: the issue follows the part


@dataclass(slots=True)
class SmellReport:
    smells: list[Smell] = field(default_factory=list)
    metrics: ArchitectureMetrics = field(default_factory=ArchitectureMetrics)
    counts: dict[str, int] = field(default_factory=dict)  # rule -> parts affected


def _names(items: list[str]) -> str:
    shown = ", ".join(items[:LIST_LIMIT])
    return shown + (f" and {len(items) - LIST_LIMIT} more" if len(items) > LIST_LIMIT else "")


def _by_source(deps: Iterable[FileDependency]) -> dict[str, list[FileDependency]]:
    grouped: dict[str, list[FileDependency]] = defaultdict(list)
    for dep in deps:
        grouped[dep.source_path].append(dep)
    return grouped


def _first_line(deps: list[FileDependency]) -> int | None:
    lines = [d.line for d in deps if d.line is not None]
    return min(lines) if lines else None


def detect(
    files: Iterable[CodeFile],
    types: Iterable[CodeType],
    dependencies: Iterable[Dependency],
    *,
    exact_limit: int = EXACT_CUT_EDGE_LIMIT,
) -> SmellReport:
    files, dependencies = list(files), list(dependencies)
    metrics = compute(files, types, dependencies, exact_limit=exact_limit)
    report = SmellReport(metrics=metrics, counts=dict.fromkeys(RULES, 0))
    component, deps = file_dependencies(files, dependencies)
    report.smells.extend(_cycles(metrics, deps, report))
    report.smells.extend(_unstable(metrics, deps, report))
    report.smells.extend(_hubs(metrics, component, deps, report))
    return report


def _anchor(own: list[FileDependency]) -> tuple[str, int | None, list[str]]:
    """A part's anchor: its first file (by path) with the evidence, at that file's first line;
    and all its files with the evidence."""
    by_file = _by_source(own)
    files = sorted(by_file)
    return files[0], _first_line(by_file[files[0]]), files


def _by_part(deps: Iterable[FileDependency]) -> dict[str, list[FileDependency]]:
    grouped: dict[str, list[FileDependency]] = defaultdict(list)
    for dep in deps:
        grouped[dep.source_component].append(dep)
    return grouped


def _cycles(
    metrics: ArchitectureMetrics, deps: list[FileDependency], report: SmellReport
) -> list[Smell]:
    cycle_of = {part: i for i, cycle in enumerate(metrics.cycles) for part in cycle.components}
    inside = [
        d
        for d in deps
        if d.source_component in cycle_of
        and cycle_of.get(d.target_component) == cycle_of[d.source_component]
    ]
    report.counts[CYCLE] = len(cycle_of)
    # Per cycle, once: the cut and the member list every part repeats.
    cuts = [{(e.source, e.target): e.weight for e in c.cut} for c in metrics.cycles]
    members = [_names(c.components) for c in metrics.cycles]
    smells: list[Smell] = []
    for part, own in sorted(_by_part(inside).items()):
        index = cycle_of[part]
        cycle, cut = metrics.cycles[index], cuts[index]
        path, line, files = _anchor(own)
        targets = sorted({d.target_component for d in own})
        to_cut = [t for t in targets if (part, t) in cut]
        if len(cycle.components) == 2:
            other = cycle.components[1] if cycle.components[0] == part else cycle.components[0]
            title = f"Dependency cycle: {part} ↔ {other}"
        else:
            title = f"Dependency cycle among {len(cycle.components)} parts, including {part}"
        message = (
            f"{part} uses {_names(targets)}, and "
            f"{'that part depends' if len(targets) == 1 else 'those parts depend'} back on "
            f"{part}: {len(cycle.components)} parts form a cycle ({members[index]}). Parts in "
            "a cycle can only change, be tested and be released together. Files of "
            f"{part} that take part: {_names(files)}."
        )
        if to_cut:
            cut_text = _names([f"{t} ({cut[(part, t)]} imports)" for t in to_cut])
            message += (
                f" The {'cheapest' if cycle.exact else 'suggested'} way to break the cycle "
                f"includes removing {part}'s use of {cut_text}."
            )
        smells.append(
            Smell(
                rule_id=CYCLE,
                path=path,
                line=line,
                component=part,
                identity=f"cycle|{part}",
                title=title[:300],
                message=message,
                details={
                    "component": part,
                    "cycle_parts": len(cycle.components),
                    "cycle": cycle.components[:20],
                    "uses_in_cycle": targets[:20],
                    "files": files[:20],
                    "file_count": len(files),
                    "cut_from_here": {t: cut[(part, t)] for t in to_cut[:20]},
                    "cut_exact": cycle.exact,
                },
                anchor_key=f"cycle|{part}",
            )
        )
    return smells


def _unstable(
    metrics: ArchitectureMetrics, deps: list[FileDependency], report: SmellReport
) -> list[Smell]:
    instability = {c.key: c.instability for c in metrics.components}
    cycle_of = {part: i for i, cycle in enumerate(metrics.cycles) for part in cycle.components}
    uses: dict[str, list[str]] = defaultdict(list)
    for edge in metrics.edges:
        # Inside a cycle the parts can only change together: reported once, as the cycle.
        same_cycle = edge.source in cycle_of and cycle_of.get(edge.target) == cycle_of[edge.source]
        if not same_cycle:
            uses[edge.source].append(edge.target)
    flagged: dict[str, set[str]] = {}
    for part, targets in uses.items():
        own = instability.get(part)
        if own is None:
            continue
        worse = {
            t
            for t in targets
            if (value := instability.get(t)) is not None and value - own > UNSTABLE_DELTA
        }
        if worse and len(worse) / len(targets) >= UNSTABLE_SHARE:
            flagged[part] = worse
    report.counts[UNSTABLE] = len(flagged)
    bad = [d for d in deps if d.target_component in flagged.get(d.source_component, set())]
    smells: list[Smell] = []
    for part, own_deps in sorted(_by_part(bad).items()):
        path, line, files = _anchor(own_deps)
        less_stable = sorted(flagged[part])
        worst = max(less_stable, key=lambda t: (instability[t] or 0.0, t))
        smells.append(
            Smell(
                rule_id=UNSTABLE,
                path=path,
                line=line,
                component=part,
                identity=f"unstable|{part}",
                title=f"Unstable dependency: {part} → {worst}"[:300],
                message=(
                    f"{part} (instability {instability[part]:.2f}) uses "
                    f"{_names([f'{t} ({instability[t]:.2f})' for t in less_stable])}, which "
                    f"change more easily than {part}: {len(less_stable)} of the "
                    f"{len(uses[part])} parts it uses. Their changes ripple into {part} and "
                    f"everything that depends on it. Files of {part} that use them: "
                    f"{_names(files)}."
                ),
                details={
                    "component": part,
                    "instability": instability[part],
                    "less_stable": {t: instability[t] for t in less_stable[:20]},
                    "uses": len(uses[part]),
                    "files": files[:20],
                    "file_count": len(files),
                },
                anchor_key=f"unstable|{part}",
            )
        )
    return smells


def _upper_quartile(values: list[int]) -> float:
    return statistics.quantiles(values, n=4, method="inclusive")[2]


def _hubs(
    metrics: ArchitectureMetrics,
    component: dict[str, str],
    deps: list[FileDependency],
    report: SmellReport,
) -> list[Smell]:
    parts = metrics.components
    if len(parts) < HUB_MIN_PARTS:
        return []
    fan_in_q3 = _upper_quartile([c.fan_in for c in parts])
    fan_out_q3 = _upper_quartile([c.fan_out for c in parts])
    files_of: dict[str, list[str]] = defaultdict(list)
    for path, key in sorted(component.items()):
        files_of[key].append(path)
    links: dict[str, int] = defaultdict(int)  # file -> cross-part dependencies in and out
    for dep in deps:
        links[dep.source_path] += 1
        if dep.target in component:
            links[dep.target] += 1
    smells: list[Smell] = []
    for part in parts:
        if not (
            part.fan_in >= max(fan_in_q3, HUB_MIN_SIDE)
            and part.fan_out >= max(fan_out_q3, HUB_MIN_SIDE)
        ):
            continue
        members = files_of[part.key]
        busiest = sorted(members, key=lambda p: (-links[p], p))[:LIST_LIMIT]
        smells.append(
            Smell(
                rule_id=HUB,
                path=members[0],
                line=None,
                component=part.key,
                identity=f"hub|{part.key}",
                title=f"Hub-like part: {part.key}"[:300],
                message=(
                    f"{part.key} is used by {part.fan_in} parts and uses {part.fan_out} parts "
                    f"({part.afferent} files elsewhere depend on it; {part.efferent} of its "
                    "files depend on other parts). A change in any of them can ripple through "
                    f"it, and a change in it reaches all of them. Busiest files: "
                    f"{_names(busiest)}."
                ),
                details={
                    "component": part.key,
                    "fan_in": part.fan_in,
                    "fan_out": part.fan_out,
                    "fan_in_upper_quartile": fan_in_q3,
                    "fan_out_upper_quartile": fan_out_q3,
                    "busiest_files": busiest,
                },
                anchor_key=f"hub|{part.key}",
            )
        )
    report.counts[HUB] = len(smells)
    return smells
