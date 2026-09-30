"""Read-only view of one frozen snapshot for AI tools and verification.

``SnapshotReader`` is implemented against PostgreSQL and the artifact store by the worker and by
``InMemorySnapshot`` for tests and the offline evaluation harness. Every method is bounded and
answers only for the snapshot it was created for, so a model cannot reach other projects,
snapshots or host files through its tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

INSTRUCTION_FILE_NAMES = frozenset(
    {
        "agents.md",
        "claude.md",
        "gemini.md",
        ".cursorrules",
        ".windsurfrules",
        "copilot-instructions.md",
        ".mcp.json",
        "mcp.json",
    }
)


def is_instruction_file(path: str) -> bool:
    """Files written to steer AI assistants: untrusted data, flagged when shown to a model."""
    name = path.rsplit("/", 1)[-1].lower()
    return name in INSTRUCTION_FILE_NAMES or name.endswith(".prompt.md")


@dataclass(frozen=True, slots=True)
class FileInfo:
    path: str
    sha256: str
    language: str | None
    line_count: int


@dataclass(frozen=True, slots=True)
class SearchHit:
    path: str
    line: int
    text: str


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    name: str
    kind: str
    path: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class Relation:
    source: str
    relation: str
    target: str
    classification: str
    evidence_line: int | None


@dataclass(frozen=True, slots=True)
class FindingSummary:
    id: str
    title: str
    severity: str
    category: str
    engine: str
    rule_id: str
    path: str
    start_line: int | None
    end_line: int | None
    message: str


class SnapshotReader(Protocol):
    snapshot_id: str

    async def files(self) -> dict[str, FileInfo]:
        """Analyzable (stored, text) files of the snapshot by path."""
        ...

    async def lines(self, path: str) -> list[str]:
        """All lines of one analyzable file (without line terminators)."""
        ...

    async def search(
        self, query: str, *, path_prefix: str | None, limit: int
    ) -> list[SearchHit]: ...

    async def symbols(self, path: str, *, limit: int) -> list[SymbolInfo]: ...

    async def neighbors(self, path: str, *, limit: int) -> list[Relation]: ...

    async def findings(self, path: str | None, *, limit: int) -> list[FindingSummary]: ...


_SYMBOL = re.compile(
    r"^\s*(?:export\s+)?(?:public|private|protected|static|final|abstract|async|\s)*"
    r"(class|interface|enum|record|function|def)\s+([A-Za-z_][A-Za-z0-9_]*)"
)


@dataclass
class InMemorySnapshot:
    """Snapshot held in memory (tests, offline evaluation). Symbols use a simple heuristic."""

    snapshot_id: str
    contents: dict[str, str]
    relations: list[Relation] = field(default_factory=list)
    finding_list: list[FindingSummary] = field(default_factory=list)

    async def files(self) -> dict[str, FileInfo]:
        import hashlib

        return {
            path: FileInfo(
                path,
                hashlib.sha256(text.encode()).hexdigest(),
                path.rsplit(".", 1)[-1] if "." in path else None,
                len(text.splitlines()),
            )
            for path, text in sorted(self.contents.items())
        }

    async def lines(self, path: str) -> list[str]:
        return self.contents[path].splitlines()

    async def search(self, query: str, *, path_prefix: str | None, limit: int) -> list[SearchHit]:
        hits: list[SearchHit] = []
        needle = query.lower()
        for path, text in sorted(self.contents.items()):
            if path_prefix and not path.startswith(path_prefix):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if needle in line.lower():
                    hits.append(SearchHit(path, number, line))
                    if len(hits) >= limit:
                        return hits
        return hits

    async def symbols(self, path: str, *, limit: int) -> list[SymbolInfo]:
        found: list[SymbolInfo] = []
        for number, line in enumerate(self.contents[path].splitlines(), start=1):
            match = _SYMBOL.match(line)
            if match:
                found.append(SymbolInfo(match.group(2), match.group(1), path, number, number))
        return found[:limit]

    async def neighbors(self, path: str, *, limit: int) -> list[Relation]:
        return [r for r in self.relations if r.source == path or r.target == path][:limit]

    async def findings(self, path: str | None, *, limit: int) -> list[FindingSummary]:
        return [f for f in self.finding_list if path is None or f.path == path][:limit]
