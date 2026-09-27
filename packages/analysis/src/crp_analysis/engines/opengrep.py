"""Opengrep adapter (Java, JavaScript, TypeScript) running only the platform-owned rules.

Exit contract (Opengrep 1.30, Semgrep-compatible CLI): 0 = scan completed (findings and per-file
errors are in the JSON report); non-zero = invalid invocation or fatal error. ``// nosem``
comments are disabled (``--disable-nosem``) and ignore files are never materialized into the work
tree, so repository content cannot suppress platform rules. The self-extracting binary unpacks
once into ``<opengrep_home>/cache`` (``XDG_CACHE_HOME``); each run gets a private HOME.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from crp_analysis.engines.base import (
    Availability,
    CacheIdentity,
    CancelToken,
    EngineOutcome,
    FileProblem,
    Heartbeat,
    RawFinding,
    completed_state,
    fingerprint_config,
    redact_root,
)
from crp_analysis.engines.process import run_bounded, scrubbed_env
from crp_core.domain.states import CoverageOutcome, EngineState

RULESET_ID = "crp-opengrep-v1"
_EXTENSIONS = (".java", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx")
_RULE_ID = re.compile(r"^  - id: (\S+)$", re.MULTILINE)


def rules_path() -> Path:
    return Path(str(resources.files("crp_analysis.rules").joinpath("opengrep-rules.yml")))


def ruleset_sha256() -> str:
    return hashlib.sha256(rules_path().read_bytes()).hexdigest()


@cache
def rule_ids() -> tuple[str, ...]:
    return tuple(_RULE_ID.findall(rules_path().read_text(encoding="utf-8")))


def _canonical_rule(check_id: str) -> str | None:
    """Opengrep prefixes rule ids with the config path; map back to an owned rule id."""
    for rule in rule_ids():
        if check_id == rule or check_id.endswith("." + rule):
            return rule
    return None


class OpengrepAdapter:
    name = "opengrep"
    ruleset_id = RULESET_ID

    def __init__(
        self,
        home: Path | None,
        *,
        timeout_seconds: float,
        max_output_bytes: int,
        max_target_bytes: int,
        per_file_timeout_seconds: int = 30,
    ) -> None:
        self._home = home
        self._timeout = timeout_seconds
        self._max_output = max_output_bytes
        self._max_target = max_target_bytes
        self._file_timeout = per_file_timeout_seconds

    def is_eligible(self, path: str, language: str | None) -> bool:
        return language in {"java", "javascript", "typescript"} and path.endswith(_EXTENSIONS)

    def enabled_rules(self) -> tuple[str, ...]:
        return rule_ids()

    def cache_identity(self) -> CacheIdentity:
        # Owned rules are intra-file patterns (no cross-file taint), so results are per file.
        return CacheIdentity(
            ruleset_sha256(),
            fingerprint_config(
                "opengrep-cli-v1", self._max_target, self._file_timeout, "--disable-nosem"
            ),
        )

    def _binary(self) -> Path | None:
        return None if self._home is None else self._home / "opengrep"

    def availability(self) -> Availability:
        binary = self._binary()
        if binary is None:
            return Availability(
                False, None, "Opengrep is not configured (CRP_OPENGREP_HOME); run make engines"
            )
        if not binary.is_file():
            return Availability(
                False, None, "Opengrep binary not found under CRP_OPENGREP_HOME; run make engines"
            )
        version_file = binary.parent / "VERSION"
        version = (
            version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else None
        )
        return Availability(True, version)

    def run(
        self, root: Path, files: list[str], *, cancel: CancelToken, heartbeat: Heartbeat
    ) -> EngineOutcome:
        availability = self.availability()
        outcome = EngineOutcome(
            state=EngineState.FAILED,
            engine_version=availability.version,
            ruleset_id=RULESET_ID,
            ruleset_sha256=ruleset_sha256(),
        )
        binary = self._binary()
        if not availability.available or binary is None:
            outcome.state = EngineState.UNAVAILABLE
            outcome.error_code = "engine_unavailable"
            outcome.error_message = availability.reason
            return outcome
        work = root.parent
        report = work / "opengrep-report.json"
        args = [
            str(binary),
            "scan",
            "--config",
            str(rules_path()),
            "--json-output",
            str(report),
            "--disable-nosem",
            "--no-git-ignore",
            "--disable-version-check",
            "--quiet",
            "--timeout",
            str(self._file_timeout),
            "--max-target-bytes",
            str(self._max_target),
            "--jobs",
            "2",
            ".",
        ]
        env = scrubbed_env([], work / "home", {"XDG_CACHE_HOME": str(binary.parent / "cache")})
        result = run_bounded(
            args,
            cwd=root,
            env=env,
            timeout_seconds=self._timeout,
            max_output_bytes=self._max_output,
            cancel=cancel,
            heartbeat=heartbeat,
            label="opengrep",
        )
        outcome.exit_code = result.exit_code
        outcome.duration_ms = result.duration_ms
        if result.cancelled:
            outcome.state = EngineState.CANCELED
            outcome.error_code = "canceled"
            return _not_attempted(outcome, files, "scan canceled")
        if result.timed_out:
            outcome.error_code = "engine_timeout"
            outcome.error_message = f"Opengrep exceeded the {self._timeout:g}s time limit"
            return _not_attempted(outcome, files, "engine timed out")
        if result.exit_code != 0 or not report.is_file():
            outcome.error_code = "engine_crashed"
            outcome.error_message = redact_root(
                f"Opengrep exited with code {result.exit_code}: {result.stderr_tail[-800:]}", root
            )
            return _not_attempted(outcome, files, "engine did not complete")
        raw = report.read_bytes()
        outcome.raw_report = redact_root(raw.decode("utf-8", errors="replace"), root).encode()
        try:
            _parse(json.loads(raw), outcome, set(files), root)
        except (ValueError, KeyError, TypeError) as exc:
            outcome.state = EngineState.FAILED
            outcome.findings = []
            outcome.error_code = "malformed_output"
            outcome.error_message = f"Opengrep report could not be parsed: {type(exc).__name__}"
            return _not_attempted(outcome, files, "engine output malformed")
        return outcome


def _not_attempted(outcome: EngineOutcome, files: list[str], reason: str) -> EngineOutcome:
    outcome.attempted = []
    outcome.problems = [FileProblem(f, CoverageOutcome.NOT_ATTEMPTED.value, reason) for f in files]
    return outcome


def _relative(path: str) -> str:
    return path[2:] if path.startswith("./") else path


def _parse(data: Any, outcome: EngineOutcome, files: set[str], root: Path) -> None:
    if data.get("version"):
        outcome.engine_version = str(data["version"])
    scanned = {_relative(str(p)) for p in (data.get("paths") or {}).get("scanned", [])}
    failed: dict[str, str] = {}
    for error in data.get("errors") or []:
        path = error.get("path")
        if path:
            failed[_relative(str(path))] = redact_root(
                f"{error.get('type', 'error')}: {str(error.get('message', ''))[:300]}", root
            )
    problems = [
        FileProblem(p, CoverageOutcome.FAILED.value, r)
        for p, r in sorted(failed.items())
        if p in files
    ]
    for missing in sorted(files - scanned - set(failed)):
        problems.append(
            FileProblem(missing, CoverageOutcome.NOT_ATTEMPTED.value, "not scanned by Opengrep")
        )
    unknown_rules: set[str] = set()
    for result in data.get("results") or []:
        path = _relative(str(result["path"]))
        rule = _canonical_rule(str(result["check_id"]))
        if rule is None:
            unknown_rules.add(str(result["check_id"]))
            continue
        if path not in files:
            continue
        extra = result.get("extra") or {}
        outcome.findings.append(
            RawFinding(
                path=path,
                rule_id=rule,
                ruleset=RULESET_ID,
                engine_severity=str(extra.get("severity")) if extra.get("severity") else None,
                message=str(extra.get("message", rule)),
                start_line=int(result["start"]["line"]),
                start_column=int(result["start"]["col"]),
                end_line=int(result["end"]["line"]),
                end_column=int(result["end"]["col"]),
                rule_url=None,
            )
        )
    not_attempted = {p.path for p in problems if p.outcome == CoverageOutcome.NOT_ATTEMPTED.value}
    outcome.attempted = sorted(files - not_attempted)
    outcome.problems = problems
    outcome.state = completed_state(len(outcome.attempted), len(failed.keys() & files))
    if not_attempted and outcome.state is EngineState.SUCCEEDED:
        outcome.state = EngineState.PARTIAL
    outcome.diagnostics = {
        "errors": len(data.get("errors") or []),
        "unknown_rules": sorted(unknown_rules),
    }
