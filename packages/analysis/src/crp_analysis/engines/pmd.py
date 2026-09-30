"""PMD adapter (Java) with the trusted platform ruleset.

Exit contract (PMD 7, run with --no-fail-on-violation --no-fail-on-error): 0 = analysis completed
and the report was written (violations and per-file processing errors are in the report);
1 = unexpected error; 2 = usage error. In-source ``// NOPMD`` markers are neutralised by a random
per-run suppress marker, and annotation suppressions are reported (``--show-suppressed``) and kept
as findings because repository content may not change platform rules.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class PmdRuleset:
    """One trusted rule set run by the pinned PMD: its engine name, id, file and languages."""

    engine: str
    ruleset_id: str
    file: str
    languages: frozenset[str]


JAVA = PmdRuleset("pmd", "crp-pmd-java-v1", "pmd-java-ruleset.xml", frozenset({"java"}))
APEX = PmdRuleset("pmd-apex", "crp-pmd-apex-v1", "pmd-apex-ruleset.xml", frozenset({"apex"}))
RULESET_ID = JAVA.ruleset_id


def ruleset_path(ruleset: PmdRuleset = JAVA) -> Path:
    return Path(str(resources.files("crp_analysis.rules").joinpath(ruleset.file)))


def ruleset_sha256(ruleset: PmdRuleset = JAVA) -> str:
    return hashlib.sha256(ruleset_path(ruleset).read_bytes()).hexdigest()


_RULE_REF = re.compile(r'ref="category/(?:java|apex)/[a-z]+\.xml/([A-Za-z]+)"')


def rule_ids(ruleset: PmdRuleset = JAVA) -> tuple[str, ...]:
    return tuple(_RULE_REF.findall(ruleset_path(ruleset).read_text(encoding="utf-8")))


class PmdAdapter:
    def __init__(
        self,
        pmd_home: Path | None,
        *,
        java_heap: str,
        timeout_seconds: float,
        max_output_bytes: int,
        java_executable: Path | None = None,
        ruleset: PmdRuleset = JAVA,
    ) -> None:
        self.ruleset = ruleset
        self.name = ruleset.engine
        self.ruleset_id = ruleset.ruleset_id
        self._home = pmd_home
        self._heap = java_heap
        self._timeout = timeout_seconds
        self._max_output = max_output_bytes
        found = java_executable or (Path(p) if (p := shutil.which("java")) else None)
        self._java = found

    def is_eligible(self, path: str, language: str | None) -> bool:
        return language in self.ruleset.languages

    def enabled_rules(self) -> tuple[str, ...]:
        return rule_ids(self.ruleset)

    def cache_identity(self) -> CacheIdentity:
        # PMD rules here are single-file (no auxclasspath is supplied, so no cross-file typing).
        return CacheIdentity(
            ruleset_sha256(self.ruleset),
            fingerprint_config("pmd-cli-v1", "--show-suppressed", "UTF-8"),
        )

    def availability(self) -> Availability:
        if self._home is None:
            return Availability(
                False, None, "PMD is not configured (CRP_PMD_HOME); run make engines"
            )
        launcher = self._home / "bin" / "pmd"
        if not launcher.is_file():
            return Availability(
                False, None, "PMD launcher not found under CRP_PMD_HOME; run make engines"
            )
        if self._java is None:
            return Availability(
                False, None, "A Java runtime (java) is required for PMD and was not found"
            )
        jars = sorted((self._home / "lib").glob("pmd-core-*.jar"))
        version = jars[0].stem.removeprefix("pmd-core-") if jars else None
        return Availability(True, version)

    def run(
        self, root: Path, files: list[str], *, cancel: CancelToken, heartbeat: Heartbeat
    ) -> EngineOutcome:
        availability = self.availability()
        outcome = EngineOutcome(
            state=EngineState.FAILED,
            engine_version=availability.version,
            ruleset_id=self.ruleset_id,
            ruleset_sha256=ruleset_sha256(self.ruleset),
        )
        if not availability.available or self._home is None or self._java is None:
            outcome.state = EngineState.UNAVAILABLE
            outcome.error_code = "engine_unavailable"
            outcome.error_message = availability.reason
            return outcome
        work = root.parent
        home = work / "home"
        file_list = work / "pmd-files.txt"
        report = work / "pmd-report.json"
        file_list.write_text("".join(f"{root / f}\n" for f in files), encoding="utf-8")
        marker = f"CRP{secrets.token_hex(8)}"
        args = [
            str(self._home / "bin" / "pmd"),
            "check",
            "--no-cache",
            "--no-progress",
            "--no-fail-on-violation",
            "--no-fail-on-error",
            "--show-suppressed",
            f"--suppress-marker={marker}",
            "-R",
            str(ruleset_path(self.ruleset)),
            "--file-list",
            str(file_list),
            "-z",
            str(root),
            "-f",
            "json",
            "-r",
            str(report),
            "-e",
            "UTF-8",
        ]
        env = scrubbed_env([self._java.parent], home, {"PMD_JAVA_OPTS": f"-Xmx{self._heap}"})
        result = run_bounded(
            args,
            cwd=work,
            env=env,
            timeout_seconds=self._timeout,
            max_output_bytes=self._max_output,
            cancel=cancel,
            heartbeat=heartbeat,
            label="pmd",
        )
        outcome.exit_code = result.exit_code
        outcome.duration_ms = result.duration_ms
        if result.cancelled:
            outcome.state = EngineState.CANCELED
            outcome.error_code = "canceled"
            return self._not_attempted(outcome, files, "scan canceled")
        if result.timed_out:
            outcome.error_code = "engine_timeout"
            outcome.error_message = f"PMD exceeded the {self._timeout:g}s time limit"
            return self._not_attempted(outcome, files, "engine timed out")
        if result.exit_code != 0 or not report.is_file():
            outcome.error_code = "engine_crashed"
            outcome.error_message = redact_root(
                f"PMD exited with code {result.exit_code}: {result.stderr_tail[-800:]}", root
            )
            return self._not_attempted(outcome, files, "engine did not complete")
        raw = report.read_bytes()
        outcome.raw_report = redact_root(raw.decode("utf-8", errors="replace"), root).encode()
        try:
            data = json.loads(raw)
            self._parse(data, outcome, root, set(files))
        except (ValueError, KeyError, TypeError) as exc:
            outcome.state = EngineState.FAILED
            outcome.error_code = "malformed_output"
            outcome.error_message = f"PMD report could not be parsed: {type(exc).__name__}"
            outcome.findings = []
            return self._not_attempted(outcome, files, "engine output malformed")
        return outcome

    def _not_attempted(
        self, outcome: EngineOutcome, files: list[str], reason: str
    ) -> EngineOutcome:
        outcome.attempted = []
        outcome.problems = [
            FileProblem(f, CoverageOutcome.NOT_ATTEMPTED.value, reason) for f in files
        ]
        return outcome

    def _parse(self, data: Any, outcome: EngineOutcome, root: Path, files: set[str]) -> None:
        if data.get("configurationErrors"):
            raise ValueError("configuration errors in trusted ruleset")
        outcome.attempted = sorted(files)
        failed: dict[str, str] = {}
        for error in data.get("processingErrors", []) or []:
            path = str(error["filename"])
            message = str(error.get("message", "processing error")).split("\n", 1)[0]
            failed[path] = redact_root(message, root)[:500]
        outcome.problems = [
            FileProblem(p, CoverageOutcome.FAILED.value, r)
            for p, r in sorted(failed.items())
            if p in files
        ]
        for file in data.get("files", []) or []:
            for violation in file.get("violations", []):
                outcome.findings.append(self._finding(str(file["filename"]), violation, False))
        for suppressed_file in data.get("suppressedViolations", []) or []:
            for violation in suppressed_file.get("violations", []):
                outcome.findings.append(
                    self._finding(str(suppressed_file["filename"]), violation, True)
                )
        outcome.findings = [f for f in outcome.findings if f.path in files]
        outcome.state = completed_state(len(outcome.attempted), len(outcome.problems))
        outcome.diagnostics = {
            "processing_errors": len(failed),
            "suppressed_in_source": sum(f.suppressed_in_source for f in outcome.findings),
            "pmd_report_version": str(data.get("pmdVersion", "")),
        }

    def _finding(self, path: str, violation: Any, suppressed: bool) -> RawFinding:
        message = str(violation["description"])
        if suppressed:
            message += " (suppressed in source; platform policy reports it anyway)"
        return RawFinding(
            path=path,
            rule_id=str(violation["rule"]),
            ruleset=self.ruleset_id,
            engine_severity=str(violation.get("priority")) if violation.get("priority") else None,
            message=message,
            start_line=int(violation["beginline"]),
            start_column=int(violation["begincolumn"]) if violation.get("begincolumn") else None,
            end_line=int(violation.get("endline") or violation["beginline"]),
            end_column=int(violation["endcolumn"]) if violation.get("endcolumn") else None,
            rule_url=str(violation["externalInfoUrl"])
            if violation.get("externalInfoUrl")
            else None,
            suppressed_in_source=suppressed,
        )
