"""Technology inventory from the frozen manifest and small build descriptors.

Nothing is executed. Build files are parsed as data (JSON, or XML with DOCTYPE refused). Each
indicator carries a version-confidence label: ``declared`` (read from a descriptor), ``inferred``
(derived indirectly) or ``unknown``.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

from crp_analysis.manifest import ManifestEntry
from crp_core.domain.states import FileDisposition

_JS_FRAMEWORKS = {
    "react": "React",
    "vue": "Vue",
    "@angular/core": "Angular",
    "next": "Next.js",
    "express": "Express",
    "@nestjs/core": "NestJS",
    "lwc": "Lightning Web Components",
}
_GRADLE_JAVA = re.compile(
    r"(?:sourceCompatibility\s*=\s*['\"]?(?:JavaVersion\.VERSION_)?([0-9_.]+))"
    r"|(?:JavaLanguageVersion\.of\((\d+)\))"
)
_GRADLE_BOOT = re.compile(r"org\.springframework\.boot['\"]?\)?\s+version\s+['\"]([^'\"]+)")
_JSONC_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


@dataclass(frozen=True, slots=True)
class Indicator:
    name: str
    kind: str
    version: str | None
    confidence: str
    evidence_path: str
    evidence: str


def _xml_root(text: str) -> ET.Element | None:
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        return None
    try:
        root = ET.fromstring(text)  # noqa: S314 - DOCTYPE/entities rejected above
    except ET.ParseError:
        return None
    for element in root.iter():
        if "}" in element.tag:
            element.tag = element.tag.split("}", 1)[1]
    return root


def _text(root: ET.Element, path: str) -> str | None:
    node = root.find(path)
    return node.text.strip() if node is not None and node.text else None


def _maven(path: str, text: str) -> list[Indicator]:
    root = _xml_root(text)
    if root is None:
        return [
            Indicator(
                "Maven", "build", None, "unknown", path, "pom.xml not parsed (invalid or DOCTYPE)"
            )
        ]
    found = [Indicator("Maven", "build", None, "declared", path, "pom.xml present")]
    for prop in ("maven.compiler.release", "maven.compiler.source", "java.version"):
        if value := _text(root, f"properties/{prop}"):
            found.append(Indicator("Java", "language", value, "declared", path, f"<{prop}>"))
            break
    parent_artifact = _text(root, "parent/artifactId")
    if parent_artifact == "spring-boot-starter-parent":
        found.append(
            Indicator(
                "Spring Boot",
                "framework",
                _text(root, "parent/version"),
                "declared",
                path,
                "parent spring-boot-starter-parent",
            )
        )
    return found


def _gradle(path: str, text: str) -> list[Indicator]:
    found = [Indicator("Gradle", "build", None, "declared", path, PurePosixPath(path).name)]
    if match := _GRADLE_JAVA.search(text):
        version = (match.group(1) or match.group(2) or "").replace("_", ".")
        found.append(Indicator("Java", "language", version, "declared", path, match.group(0)[:80]))
    if match := _GRADLE_BOOT.search(text):
        found.append(
            Indicator(
                "Spring Boot", "framework", match.group(1), "declared", path, "plugin version"
            )
        )
    return found


def _package_json(path: str, text: str) -> list[Indicator]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [Indicator("npm package", "build", None, "unknown", path, "package.json not parsed")]
    if not isinstance(data, dict):
        return []
    found = [Indicator("npm package", "build", None, "declared", path, "package.json present")]
    deps: dict[str, str] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            deps.update({str(k): str(v) for k, v in section.items()})
    engines = data.get("engines")
    if isinstance(engines, dict) and isinstance(engines.get("node"), str):
        found.append(
            Indicator("Node.js", "runtime", engines["node"], "declared", path, "engines.node")
        )
    if "typescript" in deps:
        found.append(
            Indicator(
                "TypeScript", "language", deps["typescript"], "declared", path, "dependency range"
            )
        )
    for package, label in _JS_FRAMEWORKS.items():
        if package in deps:
            found.append(
                Indicator(
                    label, "framework", deps[package], "declared", path, f"dependency {package}"
                )
            )
    if any(k in deps for k in ("eslint", "@eslint/js")) or "eslintConfig" in data:
        found.append(
            Indicator(
                "Project ESLint config",
                "tooling",
                deps.get("eslint"),
                "declared",
                path,
                "present but never executed; the platform uses its own trusted configuration",
            )
        )
    return found


def _tsconfig(path: str, text: str) -> list[Indicator]:
    try:
        data = json.loads(_JSONC_COMMENTS.sub("", text))
    except json.JSONDecodeError:
        return [
            Indicator(
                "TypeScript config", "build", None, "unknown", path, "tsconfig.json not parsed"
            )
        ]
    options = data.get("compilerOptions") if isinstance(data, dict) else None
    target = options.get("target") if isinstance(options, dict) else None
    return [
        Indicator(
            "TypeScript config",
            "build",
            str(target) if target else None,
            "declared" if target else "unknown",
            path,
            "compilerOptions.target",
        )
    ]


def _sfdx(path: str, text: str) -> list[Indicator]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {}
    version = data.get("sourceApiVersion") if isinstance(data, dict) else None
    return [
        Indicator(
            "Salesforce DX project",
            "platform",
            str(version) if version else None,
            "declared" if version else "unknown",
            path,
            "sourceApiVersion (deep Salesforce support arrives in Phase 4)",
        )
    ]


def _sap(path: str, text: str) -> list[Indicator]:
    return [
        Indicator(
            "SAP Commerce",
            "platform",
            None,
            "inferred",
            path,
            f"{PurePosixPath(path).name} present (deep SAP Commerce support arrives in Phase 4)",
        )
    ]


_HANDLERS = {
    "pom.xml": _maven,
    "build.gradle": _gradle,
    "build.gradle.kts": _gradle,
    "package.json": _package_json,
    "tsconfig.json": _tsconfig,
    "sfdx-project.json": _sfdx,
    "localextensions.xml": _sap,
    "extensioninfo.xml": _sap,
}


def build_inventory(entries: list[ManifestEntry], build_texts: dict[str, str]) -> dict[str, object]:
    languages: Counter[str] = Counter()
    lines: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    dispositions: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    agent_files: list[str] = []
    for entry in entries:
        dispositions[entry.disposition.value] += 1
        if entry.reason:
            reasons[entry.reason] += 1
        if entry.disposition is FileDisposition.ANALYZABLE:
            categories[entry.category or "other"] += 1
            if entry.language:
                languages[entry.language] += 1
                lines[entry.language] += entry.line_count or 0
            if entry.category == "agent_instructions":
                agent_files.append(entry.path)
    indicators: list[Indicator] = []
    for path, text in sorted(build_texts.items()):
        handler = _HANDLERS.get(PurePosixPath(path).name)
        if handler is not None:
            indicators.extend(handler(path, text))
    return {
        "languages": [
            {"language": name, "files": count, "lines": lines[name]}
            for name, count in languages.most_common()
        ],
        "categories": dict(categories),
        "dispositions": dict(dispositions),
        "reasons": dict(reasons),
        "indicators": [asdict(indicator) for indicator in indicators],
        "agent_instruction_files": sorted(agent_files)[:50],
        "notes": [
            "Inventory reads file names and small build descriptors only; no build or project "
            "script is executed.",
            "Agent instruction files (AGENTS.md, CLAUDE.md, …) are treated as untrusted data and "
            "never change platform policy.",
        ],
    }
