"""Trivy adapter: dependency vulnerabilities (lockfiles/manifests) and secrets, fully offline.

Pinned to Trivy 0.69.3, the release Aqua verified as safe after the March 2026 supply-chain
compromise (signature checked with cosign; see ADR 0007). Every run uses ``--offline-scan``,
``--skip-db-update``, ``--disable-telemetry`` and ``--skip-version-check``; the vulnerability
database is downloaded only by ``make engines``. The trusted secret config and an empty ignore
file are passed explicitly, so repository ``trivy.yaml``/``.trivyignore``/``trivy-secret.yaml``
files cannot change results. Matched secret values are never stored.

Exit contract: 0 = scan completed (``--exit-code`` left at 0); anything else = failure.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from crp_analysis.engines.base import (
    Availability,
    CancelToken,
    EngineOutcome,
    FileProblem,
    Guidance,
    Heartbeat,
    RawFinding,
    redact_root,
)
from crp_analysis.engines.process import run_bounded, scrubbed_env
from crp_core.domain.states import CoverageOutcome, EngineState

RULESET_ID = "crp-trivy-v1"
_SEVERITY = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "UNKNOWN": "info",
}
# The vulnerability DB is a bolt file; serialize scans within a worker process.
_DB_LOCK = threading.Lock()


def secret_config_path() -> Path:
    return Path(str(resources.files("crp_analysis.rules").joinpath("trivy-secret.yaml")))


class TrivyAdapter:
    name = "trivy"
    ruleset_id = RULESET_ID

    def __init__(
        self,
        home: Path | None,
        cache_dir: Path | None,
        *,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> None:
        self._home = home
        self._cache = cache_dir
        self._timeout = timeout_seconds
        self._max_output = max_output_bytes

    def is_eligible(self, path: str, language: str | None) -> bool:
        return True  # secret scanning covers every stored text file; lockfiles add vulnerabilities

    def enabled_rules(self) -> None:
        return None  # vulnerability IDs come from the database and are open-ended

    def cache_identity(self) -> None:
        return None  # lockfile/manifest results depend on other files and the database

    def db_metadata(self) -> dict[str, Any] | None:
        if self._cache is None:
            return None
        meta = self._cache / "db" / "metadata.json"
        if not meta.is_file() or not (self._cache / "db" / "trivy.db").is_file():
            return None
        data: dict[str, Any] = json.loads(meta.read_text(encoding="utf-8"))
        return data

    def ruleset_sha256(self) -> str:
        meta = self.db_metadata() or {}
        material = (
            f"trivy-db:{meta.get('UpdatedAt', 'missing')}|"
            + hashlib.sha256(secret_config_path().read_bytes()).hexdigest()
        )
        return hashlib.sha256(material.encode()).hexdigest()

    def availability(self) -> Availability:
        if self._home is None or not (self._home / "trivy").is_file():
            return Availability(
                False, None, "Trivy is not installed (CRP_TRIVY_HOME); run make engines"
            )
        version_file = self._home / "VERSION"
        version = (
            version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else None
        )
        if self.db_metadata() is None:
            return Availability(
                False,
                version,
                "Trivy vulnerability database is not downloaded; run make engines "
                "(offline scans need it)",
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
            ruleset_sha256=self.ruleset_sha256(),
        )
        if not availability.available or self._home is None or self._cache is None:
            outcome.state = EngineState.UNAVAILABLE
            outcome.error_code = "engine_unavailable"
            outcome.error_message = availability.reason
            return outcome
        work = root.parent
        report = work / "trivy-report.json"
        ignore = work / "empty.trivyignore"
        ignore.write_text("", encoding="utf-8")
        args = [
            str(self._home / "trivy"),
            "fs",
            "--cache-dir",
            str(self._cache),
            "--skip-db-update",
            "--skip-java-db-update",
            "--offline-scan",
            "--disable-telemetry",
            "--skip-version-check",
            "--no-progress",
            "--quiet",
            "--scanners",
            "vuln,secret",
            "--secret-config",
            str(secret_config_path()),
            "--ignorefile",
            str(ignore),
            "--format",
            "json",
            "--output",
            str(report),
            str(root),
        ]
        with _DB_LOCK:
            result = run_bounded(
                args,
                cwd=work,
                env=scrubbed_env([], work / "home"),
                timeout_seconds=self._timeout,
                max_output_bytes=self._max_output,
                cancel=cancel,
                heartbeat=heartbeat,
                label="trivy",
            )
        outcome.exit_code = result.exit_code
        outcome.duration_ms = result.duration_ms
        meta = self.db_metadata() or {}
        outcome.diagnostics = {
            "db_updated_at": meta.get("UpdatedAt"),
            "db_next_update": meta.get("NextUpdate"),
            "db_stale": _is_stale(meta.get("NextUpdate")),
            "scanners": ["vuln", "secret"],
        }
        if result.cancelled:
            outcome.state = EngineState.CANCELED
            outcome.error_code = "canceled"
            return _not_attempted(outcome, files, "scan canceled")
        if result.timed_out:
            outcome.error_code = "engine_timeout"
            outcome.error_message = f"Trivy exceeded the {self._timeout:g}s time limit"
            return _not_attempted(outcome, files, "engine timed out")
        if result.exit_code != 0 or not report.is_file():
            outcome.error_code = "engine_crashed"
            outcome.error_message = redact_root(
                f"Trivy exited with code {result.exit_code}: {result.stderr_tail[-800:]}", root
            )
            return _not_attempted(outcome, files, "engine did not complete")
        raw = report.read_bytes()
        try:
            data = json.loads(raw)
            outcome.raw_report = json.dumps(_scrub_report(data)).encode()
            _parse(data, outcome, set(files))
        except (ValueError, KeyError, TypeError) as exc:
            outcome.state = EngineState.FAILED
            outcome.findings = []
            outcome.error_code = "malformed_output"
            outcome.error_message = f"Trivy report could not be parsed: {type(exc).__name__}"
            return _not_attempted(outcome, files, "engine output malformed")
        return outcome


def _is_stale(next_update: object) -> bool | None:
    if not isinstance(next_update, str):
        return None
    try:
        return datetime.fromisoformat(next_update.replace("Z", "+00:00")) < datetime.now(UTC)
    except ValueError:
        return None


def _not_attempted(outcome: EngineOutcome, files: list[str], reason: str) -> EngineOutcome:
    outcome.attempted = []
    outcome.problems = [FileProblem(f, CoverageOutcome.NOT_ATTEMPTED.value, reason) for f in files]
    return outcome


def _scrub_report(data: Any) -> Any:
    """Drop secret match text and code excerpts before the raw report is stored."""
    for result in data.get("Results") or []:
        for secret in result.get("Secrets") or []:
            secret.pop("Match", None)
            secret.pop("Code", None)
    return data


def _vulnerability(target: str, vuln: Any) -> RawFinding:
    pkg = str(vuln.get("PkgName"))
    installed = str(vuln.get("InstalledVersion"))
    fixed = vuln.get("FixedVersion")
    vuln_id = str(vuln["VulnerabilityID"])
    locations = vuln.get("Locations") or []
    start = int(locations[0]["StartLine"]) if locations and locations[0].get("StartLine") else None
    end = int(locations[0].get("EndLine") or start) if start else None
    severity = _SEVERITY.get(str(vuln.get("Severity", "UNKNOWN")).upper(), "info")
    title = str(vuln.get("Title") or vuln_id)
    recommendation = (
        f"Upgrade {pkg} from {installed} to a fixed version ({fixed})."
        if fixed
        else f"No fixed version of {pkg} is published for {vuln_id}; "
        "assess exposure and apply mitigations."
    )
    return RawFinding(
        path=target,
        rule_id=vuln_id,
        ruleset=RULESET_ID,
        engine_severity=str(vuln.get("Severity")),
        message=f"{pkg} {installed} is affected by {vuln_id}: {title}"[:4000],
        start_line=start,
        start_column=None,
        end_line=end,
        end_column=None,
        rule_url=vuln.get("PrimaryURL"),
        anchor="dependency",
        severity=severity,
        category="dependencies",
        guidance=Guidance(
            title=f"{vuln_id} in {pkg} {installed}",
            explanation=str(vuln.get("Description") or title)[:1500],
            recommendation=recommendation,
            severity_rationale=f"Severity {vuln.get('Severity')} from the Trivy vulnerability "
            "database (source data, not re-assessed).",
            url=vuln.get("PrimaryURL"),
        ),
        details={
            "package": pkg,
            "installed_version": installed,
            "fixed_version": fixed,
            "vulnerability_id": vuln_id,
            "status": vuln.get("Status"),
            "purl": (vuln.get("PkgIdentifier") or {}).get("PURL"),
        },
        identity=f"{pkg}@{installed}",
    )


def _secret(target: str, secret: Any) -> RawFinding:
    rule = str(secret["RuleID"])
    title = str(secret.get("Title") or rule)
    return RawFinding(
        path=target,
        rule_id=f"secret:{rule}",
        ruleset=RULESET_ID,
        engine_severity=str(secret.get("Severity")),
        message=f"Possible {title} in source (value masked; never stored).",
        start_line=int(secret["StartLine"]),
        start_column=None,
        end_line=int(secret.get("EndLine") or secret["StartLine"]),
        end_column=None,
        rule_url=None,
        severity=_SEVERITY.get(str(secret.get("Severity", "UNKNOWN")).upper(), "info"),
        category="security",
        guidance=Guidance(
            title=f"Exposed secret: {title}",
            explanation="A value matching a known credential format is present in the source "
            "snapshot. Anyone with access to the code can use it.",
            recommendation="Remove it from source and history, rotate the credential, and load "
            "it from a secret manager at runtime.",
            severity_rationale=f"Severity {secret.get('Severity')} from Trivy's built-in secret "
            f"rule '{rule}'.",
            url=None,
        ),
        details={"secret_rule": rule, "secret_category": secret.get("Category")},
    )


def _parse(data: Any, outcome: EngineOutcome, files: set[str]) -> None:
    targets: set[str] = set()
    for result in data.get("Results") or []:
        target = str(result.get("Target", ""))
        targets.add(target)
        if target not in files:
            continue
        for vuln in result.get("Vulnerabilities") or []:
            outcome.findings.append(_vulnerability(target, vuln))
        for secret in result.get("Secrets") or []:
            outcome.findings.append(_secret(target, secret))
    outcome.attempted = sorted(files)
    outcome.problems = []
    outcome.state = EngineState.SUCCEEDED if files else EngineState.NOT_APPLICABLE
    outcome.diagnostics["dependency_targets"] = sorted(t for t in targets if t in files)
