"""Dependency upgrades as fixes (ADD-A2): a vulnerable library declared in ``pom.xml`` is set to
the version that fixes the vulnerabilities Trivy reports for it in this upload.

- The target is chosen from Trivy's fixed versions: for each vulnerability of the package, the
  lowest fixed version newer than the installed one (on the same major version when one exists,
  never a pre-release); then the highest of those, so one upgrade fixes them all.
- Only the version value changes: the dependency's ``<version>`` element, the
  ``<dependencyManagement>`` entry in the same file, or the ``<properties>`` entry its
  ``${...}`` refers to (which also upgrades other libraries that share the property; the fix says
  so). A version that comes from a parent POM or BOM is not in this file and is not guessed.
- npm lockfiles are not edited: their entries carry integrity hashes that only the registry can
  provide, and refactorX does not run package managers.

Nothing is downloaded, built or run. A newer library can change behaviour, so every proposal says
to compile and run the tests.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from posixpath import basename
from typing import TYPE_CHECKING

from crp_analysis.fixes.patching import Edit

if TYPE_CHECKING:
    from crp_analysis.fixes.recipes import FindingInfo, RecipeProposal

MAVEN_UPGRADE = "deps:maven-upgrade"
_QUALIFIERS = {
    "alpha": -5,
    "a": -5,
    "beta": -4,
    "b": -4,
    "milestone": -3,
    "m": -3,
    "rc": -2,
    "cr": -2,
    "snapshot": -1,
    "ga": 0,
    "final": 0,
    "release": 0,
    "sp": 1,
}
_ELEMENT = re.compile(
    r"^(?P<indent>\s*)<(?P<tag>[A-Za-z_][\w.\-]*)>(?P<value>[^<]*)</(?P=tag)>\s*$"
)
_PROPERTY_REF = re.compile(r"^\$\{(?P<name>[\w.\-]+)\}$")
VERSION = re.compile(r"^[0-9][0-9A-Za-z.\-_+]*$")


Key = tuple[tuple[int, int, str], ...]


def version_key(version: str) -> Key:
    """Order Maven-style versions: numbers numerically, pre-releases before the release,
    ``2.17`` equal to ``2.17.0`` and ``1.0.RELEASE``."""
    items: list[tuple[int, int, str]] = []
    for part in re.findall(r"\d+|[A-Za-z]+", version):
        if part.isdigit():
            items.append((1, int(part), ""))
            continue
        while items and items[-1] == (1, 0, ""):  # 1.0-rc1 is 1-rc1, like Maven
            items.pop()
        rank = _QUALIFIERS.get(part.lower())
        items.append((0, rank, "") if rank is not None else (0, 2, part.lower()))
    while items and items[-1] in {(1, 0, ""), (0, 0, "")}:
        items.pop()
    return (*items, (0, 0, ""))


def is_prerelease(version: str) -> bool:
    return any(rank < 0 and kind == 0 for kind, rank, _ in version_key(version))


def _major(version: str) -> int | None:
    match = re.match(r"\d+", version)
    return int(match.group()) if match else None


def fix_for(installed: str, fixed: str) -> str | None:
    """The lowest listed fixed version newer than ``installed`` (same major version first)."""
    newer = [
        v.strip()
        for v in fixed.split(",")
        if v.strip() and not is_prerelease(v.strip())
        if version_key(v.strip()) > version_key(installed)
    ]
    same = [v for v in newer if _major(v) == _major(installed)]
    pool = same or newer
    return min(pool, key=version_key) if pool else None


def target_version(installed: str, fixed_lists: Iterable[str]) -> str | None:
    """One version that fixes every listed vulnerability that has a fix."""
    chosen = [v for v in (fix_for(installed, f) for f in fixed_lists if f) if v]
    return max(chosen, key=version_key) if chosen else None


@dataclass(frozen=True, slots=True)
class _Element:
    line: int  # 1-based
    indent: str
    tag: str
    value: str


def _blocks(lines: list[str], tag: str) -> list[tuple[int, int]]:
    """(first, last) line numbers of each ``<tag>`` ... ``</tag>`` block (no nesting of tag)."""
    found: list[tuple[int, int]] = []
    start: int | None = None
    for number, line in enumerate(lines, start=1):
        if re.search(rf"<{tag}(\s[^>]*)?>", line) and start is None:
            start = number
        if f"</{tag}>" in line and start is not None:
            found.append((start, number))
            start = None
    return found


def _child(lines: list[str], first: int, last: int, tag: str) -> _Element | None:
    for number in range(first, last + 1):
        match = _ELEMENT.match(lines[number - 1])
        if match and match.group("tag") == tag:
            return _Element(number, match.group("indent"), tag, match.group("value").strip())
    return None


def _inside(blocks: list[tuple[int, int]], line: int) -> bool:
    return any(first <= line <= last for first, last in blocks)


def _declaration(lines: list[str], group: str, artifact: str) -> tuple[_Element | None, bool]:
    """The version element of the dependency (direct first, then the same file's management
    section), and whether the dependency is declared at all."""
    managed = _blocks(lines, "dependencyManagement")
    plugins = _blocks(lines, "plugins")
    declared = False
    direct: _Element | None = None
    management: _Element | None = None
    for first, last in _blocks(lines, "dependency"):
        if _inside(plugins, first):
            continue  # a build plugin's own dependency: build configuration, not the app's
        group_el = _child(lines, first, last, "groupId")
        artifact_el = _child(lines, first, last, "artifactId")
        if not (group_el and artifact_el):
            continue
        if (group_el.value, artifact_el.value) != (group, artifact):
            continue
        declared = True
        version = _child(lines, first, last, "version")
        if version is None:
            continue
        if _inside(managed, first):
            management = management or version
        else:
            direct = direct or version
    return direct or management, declared


def _property(lines: list[str], name: str) -> _Element | None:
    for first, last in _blocks(lines, "properties"):
        element = _child(lines, first, last, name)
        if element is not None:
            return element
    return None


def _users(lines: list[str], name: str) -> int:
    return sum(1 for line in lines if f"${{{name}}}" in line)


def maven_upgrade(finding: FindingInfo, text: str) -> RecipeProposal:
    from crp_analysis.fixes.recipes import (  # recipes imports this module
        AlreadyFixed,
        NoFix,
        RecipeProposal,
    )

    details = finding.details or {}
    package = str(details.get("package") or "")
    installed = str(details.get("installed_version") or "")
    if package.count(":") != 1 or not installed:
        raise NoFix("the finding does not name a Maven library and version")
    group, artifact = package.split(":")
    own = str(details.get("fixed_version") or "")
    if not fix_for(installed, own):
        raise NoFix(
            f"no fixed version of {artifact} newer than {installed} is published for this "
            "vulnerability"
        )
    others = details.get("fixed_versions")
    fixed_lists = [own, *(str(f) for f in others if f)] if isinstance(others, list) else [own]
    target = target_version(installed, fixed_lists)
    if target is None:  # unreachable: own has a fix
        raise NoFix("no fixed version is published")
    lines = text.splitlines()
    element, declared = _declaration(lines, group, artifact)
    if element is None:
        raise NoFix(
            f"{artifact}'s version is not set in this file (it comes from a parent POM, a BOM or "
            "another library); upgrade it there"
            if declared
            else f"{artifact} is not declared in this file (it comes from another library); "
            "declare the fixed version or upgrade the library that brings it in"
        )
    shared = ""
    reference = _PROPERTY_REF.match(element.value)
    if reference:
        name = reference.group("name")
        prop = _property(lines, name)
        if prop is None:
            raise NoFix(f"the version property {name} is not set in this file; upgrade it there")
        element = prop
        if _users(lines, name) > 1:
            shared = f" The property {name} is shared, so the libraries that use it move too."
    if not VERSION.match(element.value):
        raise NoFix(f"the version {element.value!r} is not a plain version; upgrade it by hand")
    if version_key(element.value) >= version_key(target):
        raise AlreadyFixed(f"{artifact} is already at {element.value} in this file")
    original = lines[element.line - 1]
    replacement = original.replace(f">{element.value}<", f">{target}<", 1)
    major = _major(target) != _major(installed)
    title = f"Upgrade {artifact} {installed} to {target}" + (
        " (new major version)" if major else ""
    )
    explanation = (
        f"{target} is the lowest version that fixes the published vulnerabilities of {artifact} "
        f"{installed} found in this upload (from Trivy's database).{shared}"
    )
    note = (
        "A newer library can change behaviour"
        + (", and a new major version usually has breaking changes" if major else "")
        + ". Compile and run the tests: refactorX does not download, build or run anything."
    )
    edit = Edit(finding.path, element.line, element.line, (original,), (replacement,))
    return RecipeProposal(MAVEN_UPGRADE, title, explanation, note, (edit,))


def applies(finding: FindingInfo) -> bool:
    """A Trivy vulnerability of a Maven library in a ``pom.xml`` with a published fix."""
    details = finding.details or {}
    return (
        finding.engine == "trivy"
        and basename(finding.path) == "pom.xml"
        and str(details.get("package") or "").count(":") == 1
        and bool(details.get("fixed_version"))
    )
