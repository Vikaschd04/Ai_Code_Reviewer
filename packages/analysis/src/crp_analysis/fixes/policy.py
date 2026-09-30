"""What a fix may change: scope, size, and no silencing of checks or tests.

A fix repairs the problem; it never hides it. Edits outside the proposal's allowed files, unsafe
paths, suppression markers (NOPMD, eslint-disable, inline eslint rule settings, @SuppressWarnings,
nosemgrep, trivy:ignore, ...), fewer test annotations or assertions, and changes to analyzer or
build configuration are refused before any validation runs.
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


def check(path: str, before: str, after: str, allowed_paths: frozenset[str]) -> list[Violation]:
    found: list[Violation] = []
    if not safe_path(path):
        found.append(Violation("unsafe_path", f"{path!r} is not a relative path inside the upload"))
    elif path not in allowed_paths:
        found.append(Violation("out_of_scope", f"{path} is outside this fix's allowed files"))
    name = PurePosixPath(path).name.lower()
    if name in _CONFIG_NAMES or "ruleset" in name or ("pmd" in name and name.endswith(".xml")):
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
    if changed_lines(before, after) > MAX_CHANGED_LINES:
        found.append(
            Violation(
                "too_large",
                f"the change touches more than {MAX_CHANGED_LINES} lines; split it or fix manually",
            )
        )
    return found
