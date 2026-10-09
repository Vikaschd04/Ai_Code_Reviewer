"""Tier 0 TypeScript type-check (P09; ADR 0017): compile-level evidence without executing code.

The platform's pinned TypeScript compiler (``engines/eslint-runner/typecheck.mjs``) reads the
project's TypeScript files and ``tsconfig.json`` as data. It runs in a bounded child process with a
scrubbed environment, confined to the checked folder; nothing from the project is executed,
imported or installed.

Errors caused by packages that are not installed (uploads never contain ``node_modules``) are
counted separately. Results compare two versions of the same files by (path, code, message), so
moved lines do not count as new errors.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from crp_analysis.engines.base import CancelToken, Heartbeat, redact_root
from crp_analysis.engines.process import run_bounded, scrubbed_env

TYPESCRIPT_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts")
CONFIG = "tsconfig.json"
RUNNER_VERSION = "crp-typecheck-v1"
NEW_LIMIT = 50


def typecheck_paths(paths: Iterable[str]) -> list[str]:
    """The files a type-check reads: TypeScript sources and declarations, and the root tsconfig."""
    return sorted(p for p in paths if p.endswith(TYPESCRIPT_EXTENSIONS) or p == CONFIG)


@dataclass(frozen=True, slots=True)
class TypeDiagnostic:
    path: str
    line: int
    column: int
    code: str
    message: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.path, self.code, self.message)


@dataclass(slots=True)
class TypeCheckResult:
    state: str  # checked | unavailable | failed | timeout | canceled
    version: str | None = None
    diagnostics: list[TypeDiagnostic] = field(default_factory=list)
    unresolved_imports: int = 0
    notes: list[str] = field(default_factory=list)
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "version": self.version,
            "diagnostics": [asdict(d) for d in self.diagnostics],
            "unresolved_imports": self.unresolved_imports,
            "notes": self.notes,
            "reason": self.reason,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TypeCheckResult:
        return cls(
            state=str(data["state"]),
            version=data.get("version"),
            diagnostics=[TypeDiagnostic(**d) for d in data.get("diagnostics") or []],
            unresolved_imports=int(data.get("unresolved_imports") or 0),
            notes=[str(n) for n in data.get("notes") or []],
            reason=data.get("reason"),
        )


def cache_key(identity: str, files: dict[str, str]) -> str:
    """Identity of a type-check: the checker and the exact (path, content hash) of every input."""
    canonical = json.dumps({"checker": identity, "files": sorted(files.items())})
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TypeChecker:
    def __init__(
        self,
        runner_dir: Path | None,
        *,
        node_executable: Path | None,
        timeout_seconds: float,
        max_output_bytes: int,
        heap_mb: int = 1024,
    ) -> None:
        self._dir = runner_dir
        self._node = node_executable or (Path(p) if (p := shutil.which("node")) else None)
        self._timeout = timeout_seconds
        self._max_output = max_output_bytes
        self._heap = heap_mb

    def availability(self) -> tuple[str | None, str | None]:
        """(TypeScript version, None) when usable, else (None, plain reason)."""
        if self._dir is None:
            return None, "The type checker is not configured on this server."
        package = self._dir / "node_modules" / "typescript" / "package.json"
        if not (self._dir / "typecheck.mjs").is_file() or not package.is_file():
            return None, "The type checker is not installed on this server."
        if self._node is None:
            return None, "Node.js is required for the type check and was not found."
        version = json.loads(package.read_text(encoding="utf-8")).get("version")
        return (str(version) if version else "unknown"), None

    def identity(self) -> str:
        """Checker identity for result caching: the runner script and the TypeScript version."""
        version, _ = self.availability()
        script = (
            hashlib.sha256((self._dir / "typecheck.mjs").read_bytes()).hexdigest()
            if self._dir is not None and (self._dir / "typecheck.mjs").is_file()
            else "none"
        )
        return f"{RUNNER_VERSION}:{version}:{script}"

    def run(
        self, root: Path, files: list[str], *, cancel: CancelToken, heartbeat: Heartbeat
    ) -> TypeCheckResult:
        version, reason = self.availability()
        if version is None or self._dir is None or self._node is None:
            return TypeCheckResult("unavailable", reason=reason)
        sources = [f for f in files if f.endswith(TYPESCRIPT_EXTENSIONS)]
        work = root.parent
        file_list = work / "typecheck-files.json"
        report = work / "typecheck-report.json"
        file_list.write_text(json.dumps(sources), encoding="utf-8")
        result = run_bounded(
            [
                str(self._node),
                f"--max-old-space-size={self._heap}",
                str(self._dir / "typecheck.mjs"),
                str(root),
                str(file_list),
                str(report),
            ],
            cwd=work,
            env=scrubbed_env([self._node.parent], work / "home"),
            timeout_seconds=self._timeout,
            max_output_bytes=self._max_output,
            cancel=cancel,
            heartbeat=heartbeat,
            label="typecheck",
        )
        if result.cancelled:
            return TypeCheckResult("canceled", version, reason="The type check was stopped.")
        if result.timed_out:
            return TypeCheckResult(
                "timeout", version, reason=f"The type check exceeded {self._timeout:g}s."
            )
        if result.exit_code != 0 or not report.is_file():
            detail = redact_root(result.stderr_tail[-300:], root)
            return TypeCheckResult(
                "failed", version, reason=f"The type checker stopped unexpectedly. {detail}"
            )
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
            diagnostics = [
                TypeDiagnostic(
                    str(d["path"]),
                    int(d["line"]),
                    int(d["column"]),
                    str(d["code"]),
                    str(d["message"]),
                )
                for d in data["diagnostics"]
            ]
        except ValueError, KeyError, TypeError:
            return TypeCheckResult("failed", version, reason="The type check report was malformed.")
        return TypeCheckResult(
            "checked",
            str(data.get("typescriptVersion") or version),
            diagnostics,
            int(data.get("unresolvedImports") or 0),
            [str(n) for n in data.get("notes") or []],
        )


def compare(before: TypeCheckResult, after: TypeCheckResult) -> dict[str, Any]:
    """Type errors the changes introduced and removed (line moves are not changes)."""
    if after.state != "checked":
        return {
            "state": after.state,
            "tool": f"TypeScript {after.version}" if after.version else None,
            "reason": after.reason,
        }
    old = Counter(d.key for d in before.diagnostics) if before.state == "checked" else Counter()
    remaining = Counter(old)
    new: list[TypeDiagnostic] = []
    for diagnostic in after.diagnostics:
        if remaining[diagnostic.key] > 0:
            remaining[diagnostic.key] -= 1
        else:
            new.append(diagnostic)
    return {
        "state": "checked",
        "tool": f"TypeScript {after.version}",
        "before": len(before.diagnostics) if before.state == "checked" else None,
        "after": len(after.diagnostics),
        "new_count": len(new),
        "new": [asdict(d) for d in new[:NEW_LIMIT]],
        "fixed": sum(remaining.values()) if before.state == "checked" else None,
        "unresolved_imports": after.unresolved_imports,
        "notes": after.notes,
        "reason": None if before.state == "checked" else before.reason,
    }
