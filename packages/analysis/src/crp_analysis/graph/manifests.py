"""Module manifests (pom.xml, package.json) and tsconfig.json, parsed defensively.

These files come from untrusted snapshots. XML with a DTD or entity declarations is refused
(no entity expansion), JSON is size-bounded by intake, and nothing is fetched or executed.
Parent POMs, Maven properties, npm workspaces globs and ``tsconfig`` ``extends`` chains are not
followed; each such gap is reported in diagnostics instead of guessed.
"""

from __future__ import annotations

import json
import posixpath
import re
import xml.etree.ElementTree as ET  # DTD/entity documents are rejected before parsing
from dataclasses import dataclass, field
from typing import Any

_POM_NS = re.compile(r"^\{[^}]*\}")
NPM_DEPENDENCY_SECTIONS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)


@dataclass(slots=True)
class DeclaredDependency:
    ecosystem: str  # maven | npm
    name: str  # groupId:artifactId or npm package name
    version: str | None
    scope: str | None
    line: int | None


@dataclass(slots=True)
class Module:
    directory: str  # "" for the snapshot root
    manifest: str  # path of pom.xml / package.json
    ecosystem: str
    name: str
    group_id: str | None = None
    dependencies: list[DeclaredDependency] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"module:{self.ecosystem}:{self.directory or '.'}"


@dataclass(slots=True)
class TsConfig:
    path: str
    directory: str
    base_url: str | None  # resolved snapshot-relative directory
    paths: dict[str, list[str]]
    has_extends: bool


def _local(tag: str) -> str:
    return _POM_NS.sub("", tag)


def _child_text(element: ET.Element, name: str) -> str | None:
    for child in element:
        if _local(child.tag) == name:
            return (child.text or "").strip() or None
    return None


def _unique_line(text: str, pattern: str) -> int | None:
    matches = [m.start() for m in re.finditer(pattern, text)]
    if len(matches) != 1:
        return None  # never guess among several candidate lines
    return text.count("\n", 0, matches[0]) + 1


def parse_pom(path: str, data: bytes) -> Module:
    directory = posixpath.dirname(path)
    module = Module(directory, path, "maven", directory or "(root)")
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        module.notes.append("pom.xml declares a DTD/entities and was not parsed (security)")
        return module
    try:
        root = ET.fromstring(data)  # noqa: S314 - no DTD present (checked above)
    except ET.ParseError:
        module.notes.append("pom.xml is not well-formed XML")
        return module
    text = data.decode("utf-8", errors="replace")
    artifact = _child_text(root, "artifactId")
    group = _child_text(root, "groupId")
    for child in root:
        if _local(child.tag) == "parent" and group is None:
            group = _child_text(child, "groupId")
    module.name = artifact or module.name
    module.group_id = group
    for child in root:
        if _local(child.tag) != "dependencies":
            continue
        for dep in child:
            if _local(dep.tag) != "dependency":
                continue
            dep_group, dep_artifact = _child_text(dep, "groupId"), _child_text(dep, "artifactId")
            if not dep_group or not dep_artifact:
                continue
            version = _child_text(dep, "version")
            if version and "${" in version:
                module.notes.append(
                    f"{dep_group}:{dep_artifact} version uses an unresolved property"
                )
            module.dependencies.append(
                DeclaredDependency(
                    "maven",
                    f"{dep_group}:{dep_artifact}",
                    version,
                    _child_text(dep, "scope"),
                    _unique_line(
                        text, rf"<artifactId>\s*{re.escape(dep_artifact)}\s*</artifactId>"
                    ),
                )
            )
    if any(_local(c.tag) == "dependencyManagement" for c in root):
        module.notes.append("dependencyManagement/parent POM inheritance is not evaluated")
    return module


def parse_package_json(path: str, data: bytes) -> Module:
    directory = posixpath.dirname(path)
    module = Module(directory, path, "npm", directory or "(root)")
    try:
        doc = json.loads(data)
    except ValueError, UnicodeDecodeError:
        module.notes.append("package.json is not valid JSON")
        return module
    if not isinstance(doc, dict):
        module.notes.append("package.json is not a JSON object")
        return module
    text = data.decode("utf-8", errors="replace")
    if isinstance(doc.get("name"), str):
        module.name = doc["name"][:200]
    for section in NPM_DEPENDENCY_SECTIONS:
        deps = doc.get(section)
        if not isinstance(deps, dict):
            continue
        for name, version in deps.items():
            if not isinstance(name, str):
                continue
            module.dependencies.append(
                DeclaredDependency(
                    "npm",
                    name[:214],
                    version if isinstance(version, str) else None,
                    section,
                    _unique_line(text, rf'"{re.escape(name)}"\s*:'),
                )
            )
    if doc.get("workspaces") is not None:
        module.notes.append("npm workspaces globs are not expanded; packages are matched by name")
    return module


def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas outside strings (tsconfig is JSONC)."""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        else:
            out.append(ch)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def parse_tsconfig(path: str, data: bytes) -> tuple[TsConfig | None, str | None]:
    directory = posixpath.dirname(path)
    try:
        doc = json.loads(_strip_jsonc(data.decode("utf-8", errors="replace")))
    except ValueError:
        return None, f"{path}: not valid JSON/JSONC"
    if not isinstance(doc, dict):
        return None, f"{path}: not a JSON object"
    raw_options = doc.get("compilerOptions")
    options: dict[str, Any] = raw_options if isinstance(raw_options, dict) else {}
    base = options.get("baseUrl") if isinstance(options.get("baseUrl"), str) else None
    base_dir = posixpath.normpath(posixpath.join(directory, base)) if base else None
    if base_dir == ".":
        base_dir = ""
    raw_paths: dict[str, Any] = options["paths"] if isinstance(options.get("paths"), dict) else {}
    paths = {
        str(k): [str(t) for t in v if isinstance(t, str)]
        for k, v in raw_paths.items()
        if isinstance(v, list)
    }
    has_extends = doc.get("extends") is not None
    note = f"{path}: 'extends' is not followed" if has_extends else None
    return TsConfig(path, directory, base_dir, paths, has_extends), note
