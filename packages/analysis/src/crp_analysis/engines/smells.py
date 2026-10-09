"""Architecture smells (P10 slice 3; ADR 0020): cycles, unstable dependencies and hub-like parts on
the upload's dependency map, in-process. Evidence class "potential": structure only.

Bound per review to the dependency map the graph step published for the same review, like the
architecture rules (ADR 0019). Files the map could not read are reported as not checked, never
as clean. Not cacheable per file: a smell depends on the whole part graph.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from crp_analysis.architecture.model import ArchitectureModel
from crp_analysis.architecture.smells import RULES, Smell, detect, thresholds
from crp_analysis.engines.architecture import NOT_IN_MAP, is_source
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
from crp_core.domain.states import CoverageOutcome, EngineState

ENGINE_VERSION = "1.0.0"
RULESET_ID = "crp-architecture-smells-v1"


def ruleset_sha256() -> str:
    identity = json.dumps({"rules": RULES, **thresholds()}, sort_keys=True)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def engine_version(graph_extractor: str) -> str:
    """The smells engine and the dependency extractor it reads (see the architecture rules)."""
    digest = hashlib.sha256(graph_extractor.encode("utf-8")).hexdigest()[:12]
    return f"{ENGINE_VERSION}+graph.{digest}"


def _finding(smell: Smell) -> RawFinding:
    return RawFinding(
        path=smell.path,
        rule_id=smell.rule_id,
        ruleset=RULESET_ID,
        engine_severity=None,
        message=smell.message,
        start_line=smell.line,
        start_column=None,
        end_line=smell.line,
        end_column=None,
        rule_url=None,
        anchor="source_span" if smell.line else "file",
        details={
            **smell.details,
            "evidence": "potential",
            **({"anchor_key": smell.anchor_key} if smell.anchor_key else {}),
        },
        identity=smell.identity,
        title=smell.title,
    )


@dataclass(slots=True)
class ArchitectureSmellsAdapter:
    """Unbound, the adapter only answers eligibility; ``bind`` gives the one used in a review."""

    model: ArchitectureModel | None = None
    version: str = ENGINE_VERSION
    unreadable: dict[str, str] = field(default_factory=dict)
    name: str = "smells"
    ruleset_id: str = RULESET_ID

    def bind(
        self, model: ArchitectureModel, *, version: str, unreadable: dict[str, str]
    ) -> ArchitectureSmellsAdapter:
        return ArchitectureSmellsAdapter(model, version, unreadable)

    def is_eligible(self, path: str, language: str | None) -> bool:
        return is_source(path, language)

    def availability(self) -> Availability:
        return Availability(True, self.version)

    def enabled_rules(self) -> tuple[str, ...] | None:
        return RULES

    def cache_identity(self) -> CacheIdentity | None:
        return None

    def run(
        self, root: Path, files: list[str], *, cancel: CancelToken, heartbeat: Heartbeat
    ) -> EngineOutcome:
        started = time.monotonic()
        outcome = EngineOutcome(
            state=EngineState.FAILED,
            engine_version=self.version,
            ruleset_id=RULESET_ID,
            ruleset_sha256=ruleset_sha256(),
        )
        model = self.model
        if model is None:
            outcome.error_code = "not_bound"
            outcome.error_message = "the dependency map was not loaded for this review"
            return outcome
        if cancel.cancelled:
            outcome.state = EngineState.CANCELED
            outcome.error_code = "canceled"
            return outcome
        heartbeat("architecture smells running")
        report = detect(model.files, model.types, model.dependencies)
        mapped = {f.path for f in model.files}
        wanted = set(files)
        outcome.attempted = sorted(files)
        outcome.problems = [
            FileProblem(
                path,
                CoverageOutcome.NOT_ATTEMPTED.value,
                self.unreadable.get(path, NOT_IN_MAP)[:500],
            )
            for path in sorted(wanted)
            if path not in mapped or path in self.unreadable
        ]
        outcome.findings = [
            _finding(s) for s in report.smells if s.path in wanted and s.path not in self.unreadable
        ]
        outcome.state = completed_state(len(outcome.attempted), len(outcome.problems))
        outcome.exit_code = 0
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
        outcome.diagnostics = {
            **thresholds(),
            "parts": len(report.metrics.components),
            "parts_affected": report.counts,
            "test_files": report.metrics.test_files,
            "generated_files": report.metrics.generated_files,
        }
        return outcome
