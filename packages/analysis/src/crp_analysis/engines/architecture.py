"""Architecture rules (P10 slice 2; ADR 0019): the project's intended architecture, checked on the
upload's dependency map in-process.

The adapter is bound per review to the project's rule version and the dependency map the graph
step published for the same review. Each breach becomes a finding anchored at the dependency's
evidence (usually the import) in the source file. Files the dependency map could not read are
reported as not checked, never as clean. Not cacheable per file: a breach depends on two files and
on the rules.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from crp_analysis import policy as scope_policy
from crp_analysis import structure
from crp_analysis.architecture.model import ArchitectureModel, is_generated
from crp_analysis.architecture.rules import LAYERS_RULE, RuleSet, Violation, evaluate
from crp_analysis.engines.base import (
    Availability,
    CacheIdentity,
    CancelToken,
    EngineOutcome,
    FileProblem,
    Guidance,
    Heartbeat,
    RawFinding,
    completed_state,
)
from crp_core.domain.states import CoverageOutcome, EngineState

ENGINE_VERSION = "1.0.0"
RULESET_ID = "crp-architecture-rules-v1"
NOT_IN_MAP = "not in the dependency map of this review"


def engine_version(graph_extractor: str) -> str:
    """The rules engine and the dependency extractor it reads: a newer extractor can find other
    dependencies, so absences across extractor versions are not verified fixes."""
    digest = hashlib.sha256(graph_extractor.encode("utf-8")).hexdigest()[:12]
    return f"{ENGINE_VERSION}+graph.{digest}"


def is_source(path: str, language: str | None) -> bool:
    """Files architecture checks apply to: parsed by the dependency map, not test or generated
    code."""
    return (
        structure.grammar_for(path, language) is not None
        and scope_policy.classify(path).category != "test"
        and not is_generated(path)
    )


_LAYERS_GUIDANCE = (
    "Your team listed layers from top to bottom; a layer may use only layers below it (or, with "
    "next-layer rules, only the one directly below). Upward or skipped uses tie layers together, "
    "so a change in a lower layer can no longer be made without the layers above.",
    "Move the dependency downwards: put the shared type in a lower layer, or invert it with an "
    "interface owned by the lower layer. If the use is intended for now, add an exception with a "
    "reason and an expiry date to the architecture rules.",
)
_FORBID_GUIDANCE = (
    "Your team's architecture rules forbid this dependency.",
    "Remove the dependency: move what you need into a part both may use, or depend on an "
    "interface in an allowed part. If the decision changed, change the rule (every change is "
    "versioned and recorded).",
)


def _finding(violation: Violation, rules_version: int | None) -> RawFinding:
    explanation, recommendation = (
        _LAYERS_GUIDANCE if violation.rule_id == LAYERS_RULE else _FORBID_GUIDANCE
    )
    if violation.reason:
        explanation = f"{explanation} Reason given: {violation.reason}"
    return RawFinding(
        path=violation.source_path,
        rule_id=violation.rule_id,
        ruleset=RULESET_ID,
        engine_severity=None,
        message=violation.message,
        start_line=violation.line,
        start_column=None,
        end_line=violation.line,
        end_column=None,
        rule_url=None,
        anchor="source_span" if violation.line else "file",
        severity=violation.severity,
        category="maintainability",
        guidance=Guidance(
            title=violation.title,
            explanation=explanation,
            recommendation=recommendation,
            severity_rationale=(
                f"Set by your team's architecture rules (severity {violation.severity})."
            ),
            url=None,
        ),
        details={
            "rule_sha256": violation.rule_sha256,
            "rules_version": rules_version,
            "target": violation.target,
            "source_component": violation.source_component,
            "target_component": violation.target_component,
            "source_layer": violation.source_layer,
            "target_layer": violation.target_layer,
        },
        # Without a line, the target keeps distinct breaches in one file apart.
        identity=None if violation.line else f"{violation.rule_id}|{violation.target}",
    )


@dataclass(slots=True)
class ArchitectureRulesAdapter:
    """Unbound, the adapter only answers eligibility; ``bind`` gives the one used in a review."""

    rules: RuleSet | None = None
    model: ArchitectureModel | None = None
    today: date | None = None
    version: str = ENGINE_VERSION
    rules_version: int | None = None
    unreadable: dict[str, str] = field(default_factory=dict)  # path -> why the map lacks it
    name: str = "architecture"
    ruleset_id: str = RULESET_ID

    def bind(
        self,
        rules: RuleSet,
        model: ArchitectureModel,
        *,
        today: date,
        version: str,
        rules_version: int | None,
        unreadable: dict[str, str],
    ) -> ArchitectureRulesAdapter:
        return ArchitectureRulesAdapter(rules, model, today, version, rules_version, unreadable)

    def is_eligible(self, path: str, language: str | None) -> bool:
        return is_source(path, language)

    def availability(self) -> Availability:
        return Availability(True, self.version)

    def enabled_rules(self) -> tuple[str, ...] | None:
        return tuple(self.rules.rule_ids()) if self.rules else ()

    def cache_identity(self) -> CacheIdentity | None:
        return None

    def run(
        self, root: Path, files: list[str], *, cancel: CancelToken, heartbeat: Heartbeat
    ) -> EngineOutcome:
        started = time.monotonic()
        rules, model = self.rules, self.model
        outcome = EngineOutcome(
            state=EngineState.FAILED,
            engine_version=self.version,
            ruleset_id=RULESET_ID,
            ruleset_sha256=rules.sha256() if rules else None,
        )
        if rules is None or model is None or self.today is None:
            outcome.error_code = "not_bound"
            outcome.error_message = "architecture rules were not loaded for this review"
            return outcome
        if cancel.cancelled:
            outcome.state = EngineState.CANCELED
            outcome.error_code = "canceled"
            return outcome
        heartbeat("architecture rules running")
        evaluation = evaluate(rules, model.files, model.dependencies, today=self.today)
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
            _finding(v, self.rules_version)
            for v in evaluation.violations
            if v.source_path in wanted and v.source_path not in self.unreadable
        ]
        outcome.state = completed_state(len(outcome.attempted), len(outcome.problems))
        outcome.exit_code = 0
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
        layers: dict[str, int] = {}
        for layer in evaluation.layer_of.values():
            if layer is not None:
                layers[layer] = layers.get(layer, 0) + 1
        outcome.diagnostics = {
            "rule_hashes": evaluation.rule_hashes,
            "rules_version": self.rules_version,
            "parts": len(evaluation.layer_of),
            "parts_by_layer": layers,
            "unassigned_parts": len(evaluation.unassigned),
            "overlapping_parts": len(evaluation.overlaps),
            "dependencies_checked": evaluation.dependencies_checked,
            "allowed_by_exception": evaluation.allowed,
            "expired_exceptions": [
                {"from": a.source, "to": a.target, "until": a.until.isoformat()}
                for a in evaluation.expired
                if a.until is not None
            ],
            "notes": evaluation.unmatched[:20],
        }
        return outcome
