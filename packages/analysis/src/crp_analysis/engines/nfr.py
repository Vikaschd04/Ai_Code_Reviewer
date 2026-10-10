"""Configuration and infrastructure checks for non-functional requirements (P12 slice 2, ADR 0023).

Runs in-process over YAML, Spring Boot properties and Terraform files (``crp_analysis.nfr.config``):
gaps become findings with the normal lifecycle; supporting signals (file and line) are stored in
the run's diagnostics for the NFR questionnaire and the insights. Files are read as data; nothing
is rendered, executed or downloaded. Templates that only become YAML at deploy time are reported
as not attempted, unreadable files as failed, never as clean. Not cacheable per file (overlays and
autoscalers span files).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from crp_analysis import policy as scope_policy
from crp_analysis.engines.base import (
    Availability,
    CacheIdentity,
    CancelToken,
    EngineOutcome,
    FileProblem,
    Heartbeat,
    RawFinding,
    completed_state,
)
from crp_analysis.nfr import config
from crp_analysis.nfr.signals import MAX_LOCATIONS
from crp_core.domain.states import CoverageOutcome, EngineState

ENGINE_VERSION = "1.0.0"
RULESET_ID = "crp-nfr-config-v1"


def ruleset_sha256() -> str:
    identity = [ENGINE_VERSION, config.CONFIG_VERSION, *config.RULES]
    return hashlib.sha256("\x1f".join(identity).encode()).hexdigest()


def signal_summary(hits: list[config.Hit]) -> list[dict[str, object]]:
    """Signals for the run's diagnostics: id, count and up to ``MAX_LOCATIONS`` locations."""
    grouped: dict[str, dict[str, object]] = {}
    for hit in sorted(hits, key=lambda h: (h.signal, h.path, h.line or 0)):
        entry = grouped.setdefault(hit.signal, {"signal": hit.signal, "count": 0, "locations": []})
        entry["count"] = int(str(entry["count"])) + 1
        locations = entry["locations"]
        if isinstance(locations, list) and len(locations) < MAX_LOCATIONS:
            locations.append([hit.path, hit.line, hit.detail])
    return list(grouped.values())


class ConfigChecksAdapter:
    name = "nfr"
    ruleset_id = RULESET_ID

    def __init__(self, max_file_bytes: int = 2 * 1024 * 1024) -> None:
        self._max = max_file_bytes

    def is_eligible(self, path: str, language: str | None) -> bool:
        return config.is_candidate(path) and scope_policy.classify(path).category != "test"

    def availability(self) -> Availability:
        return Availability(True, ENGINE_VERSION)

    def enabled_rules(self) -> tuple[str, ...]:
        return config.RULES

    def cache_identity(self) -> CacheIdentity | None:
        return None

    def run(
        self, root: Path, files: list[str], *, cancel: CancelToken, heartbeat: Heartbeat
    ) -> EngineOutcome:
        started = time.monotonic()
        outcome = EngineOutcome(
            state=EngineState.FAILED,
            engine_version=ENGINE_VERSION,
            ruleset_id=RULESET_ID,
            ruleset_sha256=ruleset_sha256(),
        )
        texts: dict[str, str] = {}
        too_large: dict[str, str] = {}
        for path in files:
            if cancel.cancelled:
                outcome.state = EngineState.CANCELED
                outcome.error_code = "canceled"
                return outcome
            data = (root / path).read_bytes()
            if len(data) > self._max:
                too_large[path] = f"larger than {self._max // 1024} KB; not read"
                continue
            texts[path] = data.decode("utf-8", errors="replace")
        heartbeat("configuration checks running")
        found = config.analyse(texts)
        for gap in found.gaps:
            outcome.findings.append(
                RawFinding(
                    path=gap.path,
                    rule_id=gap.rule_id,
                    ruleset=RULESET_ID,
                    engine_severity=None,
                    message=gap.message,
                    start_line=gap.line,
                    start_column=None,
                    end_line=gap.line,
                    end_column=None,
                    rule_url=None,
                    anchor="source_span" if gap.line else "file",
                    severity=gap.severity,
                    identity=gap.identity,
                    title=gap.title,
                )
            )
        skipped = {**too_large, **found.skipped}
        outcome.attempted = sorted(set(files) - skipped.keys())
        failed, not_attempted = CoverageOutcome.FAILED.value, CoverageOutcome.NOT_ATTEMPTED.value
        outcome.problems = [FileProblem(p, failed, r) for p, r in sorted(found.failed.items())] + [
            FileProblem(p, not_attempted, r) for p, r in sorted(skipped.items())
        ]
        outcome.state = completed_state(len(outcome.attempted), len(found.failed))
        if skipped and outcome.state in {EngineState.SUCCEEDED, EngineState.NOT_APPLICABLE}:
            outcome.state = EngineState.PARTIAL  # templates or large files were not checked
        outcome.exit_code = 0
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
        outcome.diagnostics = {
            "config_version": config.CONFIG_VERSION,
            "workloads": found.workloads,
            "spring_files": found.spring_files,
            "templates": len(found.skipped),
            "signals": signal_summary(found.hits),
        }
        return outcome
