"""A code tour for newcomers (ADD-A7): a one-page brief of an upload from what refactorX already
knows — languages and technologies (inventory), its parts and how they depend on each other
(dependency map), where to start reading (the files most others use), likely entry points
(``main`` methods and functions), and the files with the most open issues. Every item names the
files it comes from; nothing is inferred beyond the evidence, and nothing here uses AI.
"""

from __future__ import annotations

import posixpath
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from crp_analysis.architecture.metrics import CodeFile, component_of
from crp_analysis.architecture.model import ArchitectureModel

MAX_PARTS = 8
MAX_FILES = 8
_SEVERITY_WEIGHT = {"critical": 8, "high": 4, "medium": 2, "low": 1, "info": 0}
_DOCS = ("readme", "contributing", "architecture", "adr")


@dataclass(frozen=True, slots=True)
class TourPart:
    name: str  # package or folder
    files: int
    lines: int
    uses: list[str]  # other parts it depends on, most used first
    used_by: int  # how many other parts depend on it


@dataclass(frozen=True, slots=True)
class TourFile:
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class Tour:
    languages: list[tuple[str, int, int]]  # (language, files, lines)
    technologies: list[str]
    parts: list[TourPart] = field(default_factory=list)
    start_here: list[TourFile] = field(default_factory=list)
    entry_points: list[TourFile] = field(default_factory=list)
    hotspots: list[TourFile] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    map_available: bool = False


def _languages(inventory: Mapping[str, object]) -> list[tuple[str, int, int]]:
    raw = inventory.get("languages")
    found: list[tuple[str, int, int]] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and isinstance(item.get("language"), str):
            found.append(
                (item["language"], int(item.get("files") or 0), int(item.get("lines") or 0))
            )
    return found


def _technologies(inventory: Mapping[str, object]) -> list[str]:
    raw = inventory.get("indicators")
    names: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            version = item.get("version")
            label = (
                f"{item['name']} {version}"
                if isinstance(version, str) and version
                else item["name"]
            )
            if label not in names:
                names.append(label)
    return names[:12]


def _code(files: Iterable[CodeFile]) -> dict[str, CodeFile]:
    return {f.path: f for f in files if not f.test and not f.generated}


def build_tour(
    model: ArchitectureModel | None,
    inventory: Mapping[str, object],
    symbols: Iterable[tuple[str, str, str]],
    issues: Iterable[tuple[str, str]],
    paths: Iterable[str],
) -> Tour:
    """``symbols``: (path, kind, name); ``issues``: (path, severity) of open issues; ``paths``:
    every analyzable file of the upload."""
    all_paths = list(paths)
    docs = sorted(
        p
        for p in all_paths
        if posixpath.basename(p).lower().split(".")[0] in _DOCS
        or p.lower().startswith(("docs/", "doc/"))
    )[:MAX_FILES]
    weights: Counter[str] = Counter()
    for path, severity in issues:
        weights[path] += _SEVERITY_WEIGHT.get(severity, 0)
    hotspots = [
        TourFile(path, f"open issues weigh {score} (critical 8, high 4, medium 2, low 1)")
        for path, score in weights.most_common(MAX_FILES)
        if score > 0
    ]
    entry = sorted(
        {path for path, kind, name in symbols if name == "main" and kind in {"method", "function"}}
    )[:MAX_FILES]
    entry_points = [TourFile(path, "has a main method or function") for path in entry]
    languages, technologies = _languages(inventory), _technologies(inventory)
    if model is None:
        return Tour(languages, technologies, [], [], entry_points, hotspots, docs, False)
    files = _code(model.files)
    fan_in: Counter[str] = Counter()
    part_of = {path: component_of(f)[0] for path, f in files.items()}
    uses: dict[str, Counter[str]] = defaultdict(Counter)
    for dep in model.dependencies:
        if dep.source_path not in files or dep.target_path not in files:
            continue
        if dep.source_path != dep.target_path:
            fan_in[dep.target_path] += 1
        source_part, target_part = part_of[dep.source_path], part_of[dep.target_path]
        if source_part != target_part:
            uses[source_part][target_part] += 1
    sizes: Counter[str] = Counter()
    lines: Counter[str] = Counter()
    for path, f in files.items():
        sizes[part_of[path]] += 1
        lines[part_of[path]] += f.lines or 0
    used_by: Counter[str] = Counter()
    for targets in uses.values():
        for target in targets:
            used_by[target] += 1
    parts = [
        TourPart(
            name,
            count,
            lines[name],
            [t for t, _ in uses[name].most_common(4)],
            used_by[name],
        )
        for name, count in sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_PARTS]
    ]
    start_here = [
        TourFile(path, f"used by {count} other file{'s' if count != 1 else ''}")
        for path, count in sorted(fan_in.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_FILES]
    ]
    return Tour(languages, technologies, parts, start_here, entry_points, hotspots, docs, True)
