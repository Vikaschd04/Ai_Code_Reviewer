"""Engine adapter contract: capabilities, availability, bounded run, normalized outcome.

Adapters never report a failure as clean. Missing executables yield UNAVAILABLE with a reason;
crashes, timeouts and malformed output yield FAILED; per-file processing errors yield PARTIAL.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from crp_core.domain.states import EngineState


@dataclass(frozen=True, slots=True)
class Availability:
    available: bool
    version: str | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CacheIdentity:
    """What besides engine version, path and content determines a per-file result."""

    ruleset_sha256: str
    config_fingerprint: str


def fingerprint_config(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Guidance:
    """Engine-supplied guidance for findings outside the static catalog (e.g. a CVE)."""

    title: str
    explanation: str
    recommendation: str
    severity_rationale: str
    url: str | None


@dataclass(frozen=True, slots=True)
class RawFinding:
    path: str
    rule_id: str
    ruleset: str | None
    engine_severity: str | None
    message: str
    start_line: int | None
    start_column: int | None
    end_line: int | None
    end_column: int | None
    rule_url: str | None
    suppressed_in_source: bool = False
    anchor: str = "source_span"  # source_span | file | dependency
    severity: str | None = None  # canonical override (engine-native severity mapping)
    category: str | None = None
    guidance: Guidance | None = None
    details: dict[str, object] | None = None
    identity: str | None = None  # stable text used instead of the source line for fingerprints


@dataclass(frozen=True, slots=True)
class FileProblem:
    path: str
    outcome: str  # CoverageOutcome value: FAILED or NOT_ATTEMPTED
    reason: str


@dataclass(slots=True)
class EngineOutcome:
    state: EngineState
    engine_version: str | None
    ruleset_id: str | None
    ruleset_sha256: str | None
    attempted: list[str] = field(default_factory=list)
    problems: list[FileProblem] = field(default_factory=list)
    findings: list[RawFinding] = field(default_factory=list)
    exit_code: int | None = None
    duration_ms: int | None = None
    raw_report: bytes | None = None
    error_code: str | None = None
    error_message: str | None = None
    diagnostics: dict[str, object] = field(default_factory=dict)


class CancelToken:
    """Thread-safe cancellation flag passed from the async activity into blocking work."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


Heartbeat = Callable[[str], None]


class EngineAdapter(Protocol):
    name: str
    ruleset_id: str

    def is_eligible(self, path: str, language: str | None) -> bool: ...

    def availability(self) -> Availability: ...

    def enabled_rules(self) -> tuple[str, ...] | None:
        """Rule ids this adapter can report; None when open-ended (e.g. a CVE database)."""
        ...

    def cache_identity(self) -> CacheIdentity | None:
        """None when per-file results depend on other files (not cacheable per file)."""
        ...

    def run(
        self, root: Path, files: list[str], *, cancel: CancelToken, heartbeat: Heartbeat
    ) -> EngineOutcome: ...


def completed_state(attempted: int, failed: int) -> EngineState:
    if attempted == 0:
        return EngineState.NOT_APPLICABLE
    if failed == 0:
        return EngineState.SUCCEEDED
    if failed >= attempted:
        return EngineState.FAILED
    return EngineState.PARTIAL


def redact_root(text: str, root: Path) -> str:
    """Remove absolute work-directory prefixes from tool messages."""
    for prefix in {str(root), str(root.resolve())}:
        text = text.replace(prefix + "/", "").replace(prefix, ".")
    return text
