"""What changed between two snapshots, what it may affect, and which findings are new (P06).

It works from manifests, not from Git history, so every source type behaves the same way.
- A rename is a file that disappeared and reappeared elsewhere with identical content.
- A pull request's findings are compared with the merge base's findings.
  - A finding counts as unchanged when its fingerprint matches.
  - In a renamed file, it also counts as unchanged when engine, rule and evidence line match the
    old path.
  - Everything else is new.

Configuration files (builds, dependency locks, framework metadata) are called out because they can
change results in files that did not change. The checks that read them are not cached per file, so
they always run on the whole snapshot; the change set only explains the scope.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from crp_analysis import policy

_CONFIG_NAMES = frozenset(
    {
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "gradle.properties",
        "gradle.lockfile",
        "package.json",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "tsconfig.json",
        "sfdx-project.json",
        "localextensions.xml",
        "extensioninfo.xml",
        "project.properties",
        "manifest.json",
    }
)
_CONFIG_SUFFIXES = (
    "-items.xml",
    "-spring.xml",
    "-beans.xml",
    ".impex",
    ".permissionset-meta.xml",
    ".object-meta.xml",
    ".field-meta.xml",
    ".flow-meta.xml",
    ".md-meta.xml",
)
_LISTED = 200


def is_configuration(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    if name in _CONFIG_NAMES or name.endswith(_CONFIG_SUFFIXES):
        return True
    return policy.classify(path).category == "build"


@dataclass(frozen=True, slots=True)
class ChangeSet:
    added: tuple[str, ...]
    modified: tuple[str, ...]
    removed: tuple[str, ...]
    renamed: tuple[tuple[str, str], ...]  # (old path, new path)

    @property
    def touched(self) -> frozenset[str]:
        """Paths in the newer snapshot whose content is new or changed (renames included)."""
        return frozenset((*self.added, *self.modified, *(new for _, new in self.renamed)))

    @property
    def renames(self) -> dict[str, str]:
        """New path -> old path."""
        return {new: old for old, new in self.renamed}

    @property
    def configuration(self) -> tuple[str, ...]:
        paths = (*self.added, *self.modified, *self.removed, *(new for _, new in self.renamed))
        return tuple(sorted({p for p in paths if is_configuration(p)}))

    @property
    def empty(self) -> bool:
        return not (self.added or self.modified or self.removed or self.renamed)

    def as_dict(self, impacted: Iterable[str] = ()) -> dict[str, object]:
        impacted_list = sorted(impacted)

        def listed(paths: Iterable[str]) -> dict[str, object]:
            items = sorted(paths)
            return {"count": len(items), "paths": items[:_LISTED]}

        return {
            "added": listed(self.added),
            "modified": listed(self.modified),
            "removed": listed(self.removed),
            "renamed": {
                "count": len(self.renamed),
                "pairs": [{"from": a, "to": b} for a, b in sorted(self.renamed)[:_LISTED]],
            },
            "configuration": listed(self.configuration),
            "impacted": listed(impacted_list),
        }


def diff_manifests(base: Mapping[str, str], head: Mapping[str, str]) -> ChangeSet:
    """Compare ``path -> sha256`` maps of two snapshots (excluded files are left out by callers)."""
    gone = {path: sha for path, sha in base.items() if path not in head}
    new = {path: sha for path, sha in head.items() if path not in base}
    modified = sorted(path for path in base.keys() & head.keys() if base[path] != head[path])
    gone_by_sha: dict[str, list[str]] = defaultdict(list)
    new_by_sha: dict[str, list[str]] = defaultdict(list)
    for path, sha in gone.items():
        gone_by_sha[sha].append(path)
    for path, sha in new.items():
        new_by_sha[sha].append(path)
    renamed: list[tuple[str, str]] = []
    for sha, olds in gone_by_sha.items():
        news = new_by_sha.get(sha, [])
        if len(olds) == 1 and len(news) == 1:  # only unambiguous exact renames
            renamed.append((olds[0], news[0]))
    moved_from = {old for old, _ in renamed}
    moved_to = {new_path for _, new_path in renamed}
    return ChangeSet(
        added=tuple(sorted(p for p in new if p not in moved_to)),
        modified=tuple(modified),
        removed=tuple(sorted(p for p in gone if p not in moved_from)),
        renamed=tuple(sorted(renamed)),
    )


def impacted_files(
    touched: Iterable[str], dependencies: Iterable[tuple[str, str]], *, limit: int = 500
) -> set[str]:
    """Unchanged files with a direct graph relation to a changed file (one hop).

    ``dependencies`` holds ``(file that depends, file it depends on)`` pairs from the newer
    snapshot's graph (imports, calls, configuration links).
    """
    changed = set(touched)
    impacted: set[str] = set()
    for source, target in dependencies:
        if target in changed and source not in changed:
            impacted.add(source)
            if len(impacted) >= limit:
                break
    return impacted


@dataclass(frozen=True, slots=True)
class FindingRef:
    id: str
    fingerprint: str
    engine: str
    rule_id: str
    path: str
    start_line: int | None
    severity: str
    title: str
    text: str | None = None  # normalized evidence line; needed only for renamed files
    rule_sha256: str | None = None  # the rule's own hash, for engines that report one


@dataclass(frozen=True, slots=True)
class FindingDiff:
    new: tuple[FindingRef, ...]
    unchanged: tuple[tuple[FindingRef, FindingRef], ...]  # (head, base)
    absent: tuple[FindingRef, ...]  # base findings not reported on the head


def diff_findings(
    base: Iterable[FindingRef], head: Iterable[FindingRef], renames: Mapping[str, str]
) -> FindingDiff:
    """Split head findings into new and unchanged; list base findings the head no longer has."""
    base_list = list(base)
    by_fingerprint = {f.fingerprint: f for f in base_list}
    moved: dict[tuple[str, str, str, str], list[FindingRef]] = defaultdict(list)
    for finding in base_list:
        if finding.text is not None:
            moved[(finding.engine, finding.rule_id, finding.path, finding.text)].append(finding)
    matched: Counter[str] = Counter()
    new: list[FindingRef] = []
    unchanged: list[tuple[FindingRef, FindingRef]] = []
    for finding in head:
        other = by_fingerprint.get(finding.fingerprint)
        if other is None and finding.path in renames and finding.text is not None:
            key = (finding.engine, finding.rule_id, renames[finding.path], finding.text)
            candidates = [c for c in moved.get(key, []) if not matched[c.id]]
            other = candidates[0] if candidates else None
        if other is not None and not matched[other.id]:
            matched[other.id] += 1
            unchanged.append((finding, other))
        else:
            new.append(finding)
    absent = tuple(f for f in base_list if not matched[f.id])
    return FindingDiff(tuple(new), tuple(unchanged), absent)
