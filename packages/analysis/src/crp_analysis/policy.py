"""Versioned scope policy shared by server-side intake and the local runner.

The API publishes this policy (``GET /v1/intake-policy``) so the runner can skip the same paths
locally (secret candidates never leave the machine in folder mode) while the server re-applies it
to every archive. Exclusion means "not stored and not reviewed"; it is always reported.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import PurePosixPath

POLICY_VERSION = "scope-v1"

EXCLUDED_DIRECTORIES: dict[str, str] = {
    ".git": "vcs_metadata",
    ".hg": "vcs_metadata",
    ".svn": "vcs_metadata",
    "node_modules": "dependency_vendor",
    "bower_components": "dependency_vendor",
    "vendor": "dependency_vendor",
    ".venv": "tooling_cache",
    "venv": "tooling_cache",
    "__pycache__": "tooling_cache",
    ".gradle": "build_output",
    "target": "build_output",
    "build": "build_output",
    "dist": "build_output",
    "out": "build_output",
    ".next": "build_output",
    ".nuxt": "build_output",
    "coverage": "build_output",
    ".nyc_output": "build_output",
    ".idea": "ide_metadata",
    ".vscode": "ide_metadata",
    ".sfdx": "tool_cache",
    ".sf": "tool_cache",
}

SECRET_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.kdbx",
    "*.ppk",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    "credentials.json",
)
SECRET_ALLOWLIST = frozenset({".env.example", ".env.sample", ".env.template", ".env.dist"})
GENERATED_PATTERNS: tuple[str, ...] = ("*.min.js", "*.min.css", "*.js.map", "*.css.map")

NESTED_ARCHIVE_SUFFIXES = frozenset(
    {".zip", ".jar", ".war", ".ear", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}
)

LANGUAGES: dict[str, str] = {
    ".java": "java",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "typescript",
    ".cls": "apex",
    ".trigger": "apex",
    ".kt": "kotlin",
    ".groovy": "groovy",
    ".gradle": "groovy",
    ".py": "python",
    ".xml": "xml",
    ".json": "json",
    ".properties": "properties",
    ".impex": "impex",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".sql": "sql",
    ".sh": "shell",
    ".md": "markdown",
}

BUILD_FILES = frozenset(
    {
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "package.json",
        "tsconfig.json",
        "sfdx-project.json",
        "localextensions.xml",
        "extensioninfo.xml",
        "build.xml",
    }
)
AGENT_INSTRUCTION_FILES = frozenset(
    {
        "agents.md",
        "claude.md",
        "gemini.md",
        ".cursorrules",
        "skill.md",
        ".mcp.json",
        "copilot-instructions.md",
    }
)
CONFIG_LANGUAGES = frozenset({"xml", "json", "properties", "yaml"})


@dataclass(frozen=True, slots=True)
class Classification:
    """Policy decision for one path; ``excluded_reason`` set means the entry is not stored."""

    excluded_reason: str | None
    language: str | None
    category: str
    nested_archive: bool


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def excluded_directory_reason(segment: str) -> str | None:
    return EXCLUDED_DIRECTORIES.get(segment)


def is_secret_candidate(name: str) -> bool:
    lowered = name.lower()
    return lowered not in SECRET_ALLOWLIST and _matches(lowered, SECRET_PATTERNS)


def _category(path: PurePosixPath, language: str | None) -> str:
    name = path.name.lower()
    parts = {part.lower() for part in path.parts[:-1]}
    if name in AGENT_INSTRUCTION_FILES:
        return "agent_instructions"
    if name in BUILD_FILES:
        return "build"
    if (
        parts & {"test", "tests", "__tests__", "spec"}
        or ".test." in name
        or ".spec." in name
        or (language == "java" and path.stem.endswith("Test"))
    ):
        return "test"
    if language in CONFIG_LANGUAGES:
        return "config"
    if language == "markdown" or path.suffix.lower() in {".txt", ".rst"}:
        return "docs"
    if language is not None:
        return "source"
    return "other"


def classify(path: str) -> Classification:
    """Classify a canonical relative POSIX path (already validated by ``paths``)."""
    pure = PurePosixPath(path)
    language = LANGUAGES.get(pure.suffix.lower())
    for segment in pure.parts[:-1]:
        if reason := excluded_directory_reason(segment):
            return Classification(reason, language, "excluded", nested_archive=False)
    name = pure.name
    if is_secret_candidate(name):
        return Classification("secret_candidate", language, "excluded", nested_archive=False)
    if _matches(name.lower(), GENERATED_PATTERNS):
        return Classification("generated_minified", language, "excluded", nested_archive=False)
    nested = pure.suffix.lower() in NESTED_ARCHIVE_SUFFIXES
    return Classification(None, language, _category(pure, language), nested_archive=nested)


def policy_document() -> dict[str, object]:
    """Serializable policy for the local runner and the UI."""
    return {
        "version": POLICY_VERSION,
        "excluded_directories": EXCLUDED_DIRECTORIES,
        "secret_patterns": list(SECRET_PATTERNS),
        "secret_allowlist": sorted(SECRET_ALLOWLIST),
        "generated_patterns": list(GENERATED_PATTERNS),
    }
