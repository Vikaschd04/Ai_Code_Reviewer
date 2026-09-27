"""Bounded context utilities for the *trusted development repository* (not customer source).

``build_context_map`` inventories files, hashes and symbols; ``build_context_pack`` renders a
task-scoped packet from approved paths. Both:

* exclude VCS internals, dependency/vendor/build output, local state (``.local``), environment
  and credential files, and any directory marked untrusted (``.crp-untrusted``);
* never follow symlinks and never import or execute indexed code (Python is read with ``ast``;
  other languages with line-oriented patterns);
* enforce explicit budgets and disclose every truncation or omission in their output.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from crp_core.artifacts import UNTRUSTED_MARKER

SCHEMA_MAP = "crp-context-map/v1"
SCHEMA_PACK = "crp-context-pack/v1"

EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".local",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "dist",
        "build",
        "coverage",
        "playwright-report",
        "test-results",
        ".sf",
        ".sfdx",
        ".idea",
        ".vscode",
        "target",
    }
)
SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "id_rsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "*.secret",
    "*secret*.json",
    "*credentials*",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "*.sqlite",
    "*.db",
)
SECRET_ALLOWLIST = frozenset({".env.example"})
GENERATED_FILES = frozenset({"uv.lock", "pnpm-lock.yaml", "package-lock.json", ".DS_Store"})
LANGUAGES = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".mjs": "javascript",
    ".md": "markdown",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".css": "css",
    ".html": "html",
    ".sql": "sql",
    ".mako": "mako",
}
_TS_EXPORT = re.compile(
    r"^export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?"
    r"(function|class|const|let|interface|type|enum)\s+([A-Za-z_$][\w$]*)"
)
_MD_HEADING = re.compile(r"^(#{1,2})\s+(.+?)\s*$")
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
_BINARY_SNIFF = 8192
_TRUNCATED_YES = "- Truncated: yes (see 'Truncation and omissions')"


class ContextError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Symbol:
    name: str
    kind: str
    line: int


@dataclass(slots=True)
class FileEntry:
    path: str
    size_bytes: int
    sha256: str
    language: str
    binary: bool
    symbols: list[Symbol] = field(default_factory=list)
    symbol_note: str | None = None


@dataclass(slots=True)
class Inventory:
    files: list[FileEntry]
    excluded: dict[str, int]
    digest: str


def _is_secret(name: str) -> bool:
    if name in SECRET_ALLOWLIST:
        return False
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in SECRET_PATTERNS)


def exclusion_reason(root: Path, relative: PurePosixPath) -> str | None:
    """Why ``relative`` (a path inside ``root``) is excluded, or None if it may be indexed."""
    current = root
    for index, part in enumerate(relative.parts):
        current = current / part
        if current.is_symlink():
            return "symlink"
        is_last = index == len(relative.parts) - 1
        if not is_last or current.is_dir():
            if part in EXCLUDED_DIRS:
                return "vendor_cache_or_local_state"
            if (current / UNTRUSTED_MARKER).exists():
                return "untrusted"
    name = relative.name
    if _is_secret(name):
        return "secret_or_environment"
    if name in GENERATED_FILES:
        return "generated"
    return None


def _hash_file(path: Path) -> tuple[str, int, bool]:
    digest = hashlib.sha256()
    size = 0
    binary = False
    with path.open("rb") as handle:
        first = True
        while chunk := handle.read(1024 * 1024):
            if first:
                binary = b"\x00" in chunk[:_BINARY_SNIFF]
                first = False
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size, binary


def extract_symbols(text: str, language: str) -> tuple[list[Symbol], str | None]:
    """Static symbol extraction. Python uses ``ast.parse`` (parsing never executes code)."""
    if language == "python":
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            return [], f"python syntax error at line {exc.lineno}"
        symbols: list[Symbol] = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                symbols.append(Symbol(node.name, "class", node.lineno))
                symbols.extend(
                    Symbol(f"{node.name}.{child.name}", "method", child.lineno)
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(Symbol(node.name, "function", node.lineno))
        return symbols, None
    if language in {"typescript", "javascript"}:
        found = []
        for number, line in enumerate(text.splitlines(), start=1):
            if match := _TS_EXPORT.match(line.strip()):
                found.append(Symbol(match.group(2), match.group(1), number))
        return found, None
    if language == "markdown":
        return [
            Symbol(match.group(2), f"h{len(match.group(1))}", number)
            for number, line in enumerate(text.splitlines(), start=1)
            if (match := _MD_HEADING.match(line))
        ], None
    return [], None


def _read_text(path: Path, limit: int) -> tuple[str, bool]:
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    return raw[:limit].decode("utf-8", errors="replace"), len(raw) > limit


def scan_inventory(root: Path, *, symbol_byte_limit: int = 256 * 1024) -> Inventory:
    root = root.resolve()
    files: list[FileEntry] = []
    excluded: dict[str, int] = {}

    def skip(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept = []
        for name in sorted(dirnames):
            child = current / name
            if child.is_symlink():
                skip("symlink")
            elif name in EXCLUDED_DIRS:
                skip("vendor_cache_or_local_state")
            elif (child / UNTRUSTED_MARKER).exists():
                skip("untrusted")
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = current / name
            relative = PurePosixPath(path.relative_to(root).as_posix())
            if path.is_symlink():
                skip("symlink")
                continue
            if name == UNTRUSTED_MARKER:
                continue
            if _is_secret(name):
                skip("secret_or_environment")
                continue
            if name in GENERATED_FILES:
                skip("generated")
                continue
            sha, size, binary = _hash_file(path)
            language = LANGUAGES.get(path.suffix.lower(), "binary" if binary else "other")
            entry = FileEntry(str(relative), size, sha, language, binary)
            if not binary:
                text, truncated = _read_text(path, symbol_byte_limit)
                entry.symbols, entry.symbol_note = extract_symbols(text, language)
                if truncated:
                    entry.symbol_note = f"symbols from first {symbol_byte_limit} bytes only"
            files.append(entry)
    files.sort(key=lambda item: item.path)
    digest = hashlib.sha256(
        "\n".join(f"{item.path}\0{item.sha256}" for item in files).encode()
    ).hexdigest()
    return Inventory(files=files, excluded=excluded, digest=digest)


def build_context_map(
    root: Path, *, max_files: int = 400, max_symbols: int = 40
) -> dict[str, object]:
    inventory = scan_inventory(root)
    selected = inventory.files[:max_files]
    omitted = len(inventory.files) - len(selected)
    symbol_truncated = 0
    rendered = []
    for entry in selected:
        data = asdict(entry)
        if len(entry.symbols) > max_symbols:
            symbol_truncated += 1
            data["symbols"] = data["symbols"][:max_symbols]
            data["symbol_note"] = f"first {max_symbols} of {len(entry.symbols)} symbols"
        rendered.append(data)
    return {
        "schema": SCHEMA_MAP,
        "generated_at": datetime.now(UTC).isoformat(),
        "root": ".",
        "worktree_digest": inventory.digest,
        "limits": {"max_files": max_files, "max_symbols_per_file": max_symbols},
        "file_count": len(inventory.files),
        "files": rendered,
        "excluded": inventory.excluded,
        "truncated": omitted > 0 or symbol_truncated > 0,
        "truncation": {
            "omitted_files": omitted,
            "files_with_truncated_symbols": symbol_truncated,
        },
    }


# -- context pack ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PackResult:
    path: Path
    cache_hit: bool
    truncated: bool
    key: str


def resolve_approved_path(root: Path, raw: str) -> PurePosixPath:
    """Validate a user-approved path: relative, inside the trusted root and not excluded."""
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(root)
        except ValueError as exc:
            raise ContextError(f"approved path is outside the trusted root: {raw}") from exc
    relative = PurePosixPath(candidate.as_posix())
    if any(part == ".." for part in relative.parts):
        raise ContextError(f"approved path must not contain '..': {raw}")
    full = root / relative
    if not full.exists():
        raise ContextError(f"approved path does not exist: {raw}")
    if relative != PurePosixPath(".") and (reason := exclusion_reason(root, relative)):
        raise ContextError(f"approved path is excluded ({reason}): {raw}")
    if root.resolve() not in (full.resolve(), *full.resolve().parents):
        raise ContextError(f"approved path is outside the trusted root: {raw}")
    return relative


def _junit_summary(reports: Path) -> list[str]:
    lines = []
    for report in sorted(reports.glob("*.xml")) if reports.is_dir() else []:
        try:
            tree = ET.parse(report)  # noqa: S314 - locally generated trusted report
        except ET.ParseError:
            lines.append(f"- {report.name}: unreadable report")
            continue
        suites = tree.getroot()
        suite_list = [suites] if suites.tag == "testsuite" else list(suites)
        totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        for suite in suite_list:
            for key in totals:
                totals[key] += int(suite.get(key, "0"))
        stamp = datetime.fromtimestamp(report.stat().st_mtime, UTC).isoformat(timespec="seconds")
        lines.append(
            f"- {report.name} ({stamp}): {totals['tests']} tests, {totals['failures']} failures, "
            f"{totals['errors']} errors, {totals['skipped']} skipped"
        )
    return lines or ["- No recorded test report under .local/test-reports (run `make test`)."]


def _phase_rows(root: Path) -> list[str]:
    status = root / "docs" / "PHASE_STATUS.md"
    if not status.is_file():
        return ["- docs/PHASE_STATUS.md not found"]
    return [
        line for line in status.read_text(encoding="utf-8").splitlines() if line.startswith("| P")
    ]


def build_context_pack(
    root: Path,
    *,
    task: str,
    approved: list[str],
    out_dir: Path,
    max_bytes: int = 60_000,
    max_file_bytes: int = 12_000,
    reports_dir: Path | None = None,
) -> PackResult:
    if not _TASK_ID.fullmatch(task):
        raise ContextError("task id must be 1-40 characters of letters, digits, '.', '_' or '-'")
    if not approved:
        raise ContextError("at least one --path is required")
    root = root.resolve()
    relatives = [resolve_approved_path(root, raw) for raw in approved]
    inventory = scan_inventory(root)

    def selected_by(entry: FileEntry) -> bool:
        path = PurePosixPath(entry.path)
        return any(
            rel == PurePosixPath(".") or rel == path or rel in path.parents for rel in relatives
        )

    selected = [entry for entry in inventory.files if selected_by(entry)]
    adrs = [entry for entry in inventory.files if entry.path.startswith("docs/adr/")]
    contracts = [
        entry
        for entry in inventory.files
        if entry.path in {"docs/API_CONTRACTS.md", "packages/contracts/openapi.json"}
    ]
    reports = reports_dir or (root / ".local" / "test-reports")
    test_lines = _junit_summary(reports)
    options = f"{max_bytes}:{max_file_bytes}"
    key_material = "\n".join(
        [task, inventory.digest, options, *test_lines, *(f"{e.path}:{e.sha256}" for e in selected)]
    )
    key = hashlib.sha256(key_material.encode()).hexdigest()[:24]
    task_dir = out_dir / task
    target = task_dir / f"{key}.md"
    if target.is_file():
        truncated = _TRUNCATED_YES in target.read_text(encoding="utf-8")
        return PackResult(target, cache_hit=True, truncated=truncated, key=key)

    budget = max_bytes
    omissions: list[str] = []
    sections: list[str] = []
    for entry in selected:
        header = f"### {entry.path}\nsha256 `{entry.sha256}`, {entry.size_bytes} bytes"
        if entry.symbols:
            names = ", ".join(f"{s.name}:{s.line}" for s in entry.symbols[:30])
            header += f"\nSymbols: {names}"
        if entry.binary:
            sections.append(header + "\n(binary; content omitted)")
            omissions.append(f"{entry.path}: binary content omitted")
            continue
        if budget <= 0:
            omissions.append(f"{entry.path}: content omitted, packet byte budget exhausted")
            sections.append(header + "\n(content omitted: budget exhausted)")
            continue
        allowance = min(max_file_bytes, budget)
        text, file_truncated = _read_text(root / entry.path, allowance)
        if file_truncated:
            shown = text.count("\n")
            omissions.append(
                f"{entry.path}: truncated after {allowance} bytes (~{shown} lines shown)"
            )
        fence = "````"
        language = entry.language if entry.language not in {"other", "binary"} else ""
        sections.append(f"{header}\n{fence}{language}\n{text}\n{fence}")
        budget -= len(text.encode("utf-8"))

    lines = [
        f"# Context packet: {task}",
        "",
        f"- Schema: {SCHEMA_PACK}",
        f"- Generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"- Trusted root: repository root (`.`); worktree digest `{inventory.digest}`",
        f"- Approved paths: {', '.join(f'`{r}`' for r in relatives)}",
        f"- Budgets: {max_bytes} bytes total, {max_file_bytes} bytes per file",
        f"- Excluded from inventory: {json.dumps(inventory.excluded, sort_keys=True)}",
        _TRUNCATED_YES if omissions else "- Truncated: no",
        "",
        "## Phase status",
        *_phase_rows(root),
        "",
        "## Recorded test status",
        *test_lines,
        "",
        "## Relevant decisions and contracts",
        *(f"- `{e.path}` sha256 `{e.sha256[:16]}`" for e in adrs + contracts),
        "",
        "## Selected files",
        *(f"- `{e.path}` ({e.language}, {e.size_bytes} bytes)" for e in selected),
        "",
        "## Truncation and omissions",
        *(f"- {item}" for item in omissions),
        *(["- none"] if not omissions else []),
        "",
        "## Source",
        *sections,
        "",
    ]
    task_dir.mkdir(parents=True, exist_ok=True)
    for stale in task_dir.glob("*.md"):
        stale.unlink()
    target.write_text("\n".join(lines), encoding="utf-8")
    return PackResult(target, cache_hit=False, truncated=bool(omissions), key=key)
