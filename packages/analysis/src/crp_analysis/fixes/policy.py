"""What a fix may change: scope, size, and no silencing of checks or tests.

A fix repairs the problem; it never hides it. Edits outside the proposal's allowed files, unsafe
paths, suppression markers (NOPMD, eslint-disable, inline eslint rule settings, @SuppressWarnings,
nosemgrep, trivy:ignore, ...), fewer test annotations or assertions, skipped or focused tests,
and changes to analyzer or build configuration are refused before any validation runs, except
a ``pom.xml`` change that only changes version values (a dependency upgrade, ADR 0027).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from crp_analysis.fixes.patching import changed_lines, safe_path

MAX_CHANGED_LINES = 60
_SUPPRESSION = re.compile(
    r"NOPMD|eslint-disable|/\*\s*eslint\s|@SuppressWarnings|\bnosem(?:grep)?\b|trivy:ignore|"
    r"NOSONAR|@ts-ignore|@ts-nocheck|@ts-expect-error|codeanalyzer-disable|istanbul\s+ignore|"
    r"\bnoqa\b",
    re.IGNORECASE,
)
_TESTS = re.compile(
    r"@Test\b|@isTest\b|\bassert\w*\s*\(|\bAssert\.|System\.assert|\bexpect\s*\(|\btestMethod\b",
    re.IGNORECASE,
)
# Disabling or focusing tests (only the focused ones run) weakens a suite without removing it.
_SKIPS = re.compile(
    r"\b(?:it|test|describe|context|suite)\.(?:skip|only|todo)\s*\(|\bx(?:it|describe|test)\s*\(|"
    r"\bf(?:it|describe)\s*\(|@Ignore\b|@Disabled\b|@unittest\.skip|pytest\.mark\.skip|"
    r"\bpending\s*\(\s*\)",
)
_CONFIG_NAMES = frozenset(
    {
        ".eslintrc",
        ".eslintrc.js",
        ".eslintrc.cjs",
        ".eslintrc.json",
        ".eslintrc.yml",
        "eslint.config.js",
        "eslint.config.mjs",
        ".semgrepignore",
        ".trivyignore",
        "trivy.yaml",
        "code-analyzer.yml",
        "code-analyzer.yaml",
        "pom.xml",
        "package.json",
        "build.gradle",
        "sfdx-project.json",
    }
)


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    message: str


_ELEMENT = re.compile(r"^\s*<(?P<tag>[A-Za-z_][\w.\-]*)>(?P<value>[^<]*)</(?P=tag)>\s*$")
_VERSION = re.compile(r"^\s*[0-9][0-9A-Za-z.\-_+]*\s*$")


def versions_only(before: str, after: str) -> bool:
    """A Maven POM change that only changes version values (ADR 0027): the same lines, and every
    changed line is one element, ``<version>`` or a property, whose old and new values are both
    versions. Plugin, analyzer or any other build settings are not."""
    old, new = before.splitlines(), after.splitlines()
    if len(old) != len(new) or old == new:
        return False
    in_properties = False
    for a, b in zip(old, new, strict=True):
        if "<properties>" in a:
            in_properties = True
        if a != b:
            first, second = _ELEMENT.match(a), _ELEMENT.match(b)
            if not (first and second) or first.group("tag") != second.group("tag"):
                return False
            if first.group("tag") != "version" and not in_properties:
                return False
            if not (_VERSION.match(first.group("value")) and _VERSION.match(second.group("value"))):
                return False
        if "</properties>" in a:
            in_properties = False
    return True


def check(path: str, before: str, after: str, allowed_paths: frozenset[str]) -> list[Violation]:
    found: list[Violation] = []
    if not safe_path(path):
        found.append(Violation("unsafe_path", f"{path!r} is not a relative path inside the upload"))
    elif path not in allowed_paths:
        found.append(Violation("out_of_scope", f"{path} is outside this fix's allowed files"))
    name = PurePosixPath(path).name.lower()
    if (
        name in _CONFIG_NAMES or "ruleset" in name or ("pmd" in name and name.endswith(".xml"))
    ) and not (name == "pom.xml" and versions_only(before, after)):
        found.append(
            Violation("config_change", "analyzer and build configuration is never changed by a fix")
        )
    if len(_SUPPRESSION.findall(after)) > len(_SUPPRESSION.findall(before)):
        found.append(
            Violation("suppression_added", "the change adds a marker that silences a check")
        )
    if len(_TESTS.findall(after)) < len(_TESTS.findall(before)):
        found.append(
            Violation("test_weakened", "the change removes test annotations or assertions")
        )
    elif len(_SKIPS.findall(after)) > len(_SKIPS.findall(before)):
        found.append(Violation("test_weakened", "the change skips or focuses tests"))
    if changed_lines(before, after) > MAX_CHANGED_LINES:
        found.append(
            Violation(
                "too_large",
                f"the change touches more than {MAX_CHANGED_LINES} lines; split it or fix manually",
            )
        )
    return found
