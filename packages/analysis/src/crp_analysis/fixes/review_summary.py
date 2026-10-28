"""A reviewer's summary of a fix workspace (ADD-A5): what changed and why, what the check showed,
what else depends on the changed files, and what still needs a person.

Impact comes from the upload's dependency map (P02 graph, P10 model): files that use a changed
file directly (imports, extends, on-demand package imports), the files that use those, the parts
(packages or folders) they belong to, and the test files that use the changed code, which are
the tests to run. It is source-level evidence, not a call graph or a build: reflection, dependency
injection and other languages' references are not seen, and the summary says so.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from crp_analysis.architecture.metrics import component_of
from crp_analysis.architecture.model import ArchitectureModel

MAX_LISTED = 20


@dataclass(frozen=True, slots=True)
class Impact:
    available: bool  # a dependency map exists for the upload
    direct: list[str] = field(default_factory=list)  # code files that use a changed file
    indirect: list[str] = field(default_factory=list)  # code files that use those
    tests: list[str] = field(default_factory=list)  # test files that use changed or direct code
    parts: list[str] = field(default_factory=list)  # packages or folders affected


def impact(model: ArchitectureModel | None, changed: Iterable[str]) -> Impact:
    """Who uses the changed files, up to two steps away (generated code left out)."""
    if model is None:
        return Impact(available=False)
    files = {f.path: f for f in model.files}
    users: dict[str, set[str]] = defaultdict(set)
    package_users: dict[str, set[str]] = defaultdict(set)
    for dep in model.dependencies:
        if dep.target_path is not None:
            users[dep.target_path].add(dep.source_path)
        elif dep.target_package is not None:
            package_users[dep.target_package].add(dep.source_path)

    def used_by(paths: set[str]) -> set[str]:
        found: set[str] = set()
        for path in paths:
            found |= users.get(path, set())
            package = files[path].package if path in files else None
            if package:
                found |= package_users.get(package, set())
        return found

    changed_set = set(changed)
    first = used_by(changed_set) - changed_set
    second = used_by(first) - first - changed_set

    def code(paths: set[str]) -> list[str]:
        return sorted(
            p for p in paths if p in files and not files[p].test and not files[p].generated
        )

    direct, indirect = code(first), code(second)
    tests = sorted(p for p in first | second if p in files and files[p].test)
    parts = sorted({component_of(files[p])[0] for p in direct + indirect})
    return Impact(True, direct, indirect, tests, parts)


@dataclass(frozen=True, slots=True)
class FileChange:
    path: str
    action: str  # modify | add | delete
    sources: tuple[str, ...]  # manual | recipe | ai
    flags: tuple[str, ...]  # change-policy notes
    reasons: tuple[str, ...]  # recorded change summaries (recipe titles, AI titles, edits)


@dataclass(frozen=True, slots=True)
class CheckState:
    checked: bool
    current: bool  # the check covers the workspace's current content
    state: str | None
    counts: Mapping[str, int]


_FLAG_WORDS = {
    "config_change": "changes build or analyzer configuration",
    "suppression_added": "adds a marker that silences a check",
    "test_weakened": "removes, skips or focuses tests",
    "too_large": "is a large change",
}


def needs_human(changes: list[FileChange], check: CheckState, impact: Impact) -> list[str]:
    """What a reviewer must look at or do before merging, most important first."""
    items: list[str] = []
    if not check.checked:
        items.append("Run the check: these changes have not been checked yet.")
    elif not check.current:
        items.append("Run the check again: the workspace changed after the last check.")
    counts = check.counts
    if check.checked and counts.get("new", 0):
        items.append(f"Look at {counts['new']} new problem(s) the check found in the changed code.")
    if check.checked and counts.get("suppressed", 0):
        items.append(
            f"{counts['suppressed']} issue(s) were hidden, not fixed: remove the markers that "
            "silence the checks."
        )
    for change in changes:
        notes = [_FLAG_WORDS[f] for f in change.flags if f in _FLAG_WORDS]
        if notes:
            items.append(f"Review {change.path}: it {', '.join(notes)}.")
    ai = sorted(c.path for c in changes if "ai" in c.sources)
    if ai:
        items.append(
            "Read the AI-suggested changes line by line ("
            + ", ".join(ai[:5])
            + (f" and {len(ai) - 5} more" if len(ai) > 5 else "")
            + ")."
        )
    if any(r.startswith("Upgrade ") for c in changes for r in c.reasons):
        items.append("Build the project and run its tests: a library was upgraded.")
    if impact.tests:
        items.append(
            "Run the tests that use the changed code: "
            + ", ".join(impact.tests[:5])
            + (f" and {len(impact.tests) - 5} more" if len(impact.tests) > 5 else "")
            + "."
        )
    elif impact.direct:
        items.append(
            f"No test file uses the changed code directly; {len(impact.direct)} file(s) depend "
            "on it — consider adding tests."
        )
    items.append(
        "refactorX checked the source only: nothing was compiled, built or tested. Let your own "
        "CI run before merging."
    )
    return items


def markdown(
    title: str,
    upload: str,
    changes: list[FileChange],
    check: CheckState,
    impact: Impact,
    todo: list[str],
) -> str:
    """The summary as Markdown for a pull request or a ticket (paths in code spans)."""
    lines = [f"# {title}", "", f"Fixes on top of {upload}.", "", "## What changed and why", ""]
    for change in changes:
        why = "; ".join(dict.fromkeys(change.reasons)) or "edited by hand"
        lines.append(f"- {change.action} `{change.path}`: {why}")
    lines += ["", "## Check", ""]
    if check.checked:
        c = check.counts
        lines.append(
            f"{c.get('fixed', 0)} fixed, {c.get('still_present', 0)} still present, "
            f"{c.get('suppressed', 0)} hidden (not fixed), {c.get('new', 0)} new"
            + ("" if check.current else " (before the latest changes)")
            + "."
        )
    else:
        lines.append("Not checked yet.")
    lines += ["", "## Impact", ""]
    if not impact.available:
        lines.append("No dependency map for this upload.")
    else:
        lines.append(
            f"{len(impact.direct)} file(s) use the changed files directly, "
            f"{len(impact.indirect)} more through them"
            + (f", in {', '.join(f'`{p}`' for p in impact.parts[:8])}" if impact.parts else "")
            + "."
        )
        for path in impact.direct[:MAX_LISTED]:
            lines.append(f"- `{path}`")
        lines.append(
            "From imports and type references in the source; reflection and dependency "
            "injection are not seen."
        )
    lines += ["", "## Needs a person", ""]
    lines += [f"- {item}" for item in todo]
    return "\n".join(lines) + "\n"


def pull_request_review_sections(found: Impact, todo: list[str]) -> str:
    """Impact and what needs a person, appended to a fix pull request's description."""
    lines = ["", "", "**Impact**"]
    if not found.available:
        lines.append("No dependency map for this upload.")
    else:
        lines.append(
            f"{len(found.direct)} file(s) use the changed files directly, "
            f"{len(found.indirect)} more through them; tests that use the changed code: "
            + (", ".join(f"`{t}`" for t in found.tests[:10]) or "none found")
            + "."
        )
    lines += ["", "**Needs a person**", *[f"- {item}" for item in todo]]
    return "\n".join(lines)
