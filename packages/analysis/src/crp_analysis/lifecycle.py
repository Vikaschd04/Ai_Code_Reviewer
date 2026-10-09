"""Strict recheck classification for a previously observed finding that is now absent.

An absent finding is only VERIFIED_ABSENT when the newer scan could actually have reported it:
the same engine completed, analyzed the same path, the rule is still enabled, and the engine
version and rule-set hash equal those of the earlier observation. Anything else is NOT_RECHECKED,
UNKNOWN or RULE_OBSOLETE - never "fixed".

Engines with per-rule configuration (architecture rules, ADR 0019) report a hash per rule: the
recheck then compares the rule's own hash, so changing one rule does not make every other rule's
absences unverifiable, and a changed rule is never mistaken for a fix.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from crp_core.domain.states import CoverageOutcome, EngineState, RecheckState

_COMPLETED = frozenset({EngineState.SUCCEEDED.value, EngineState.PARTIAL.value})


@dataclass(frozen=True, slots=True)
class RunView:
    engine: str
    state: str
    engine_version: str | None
    ruleset_sha256: str | None
    enabled_rules: frozenset[str] | None  # None: open-ended rule set (e.g. vulnerability DB)
    rule_hashes: Mapping[str, str] | None = None  # per-rule configuration hashes, if reported


def rule_hashes(diagnostics: Mapping[str, object] | None) -> dict[str, str] | None:
    """The per-rule hashes an engine run reported in its diagnostics (None: not reported)."""
    value = (diagnostics or {}).get("rule_hashes")
    if not isinstance(value, dict):
        return None
    return {str(k): v for k, v in value.items() if isinstance(v, str)}


def observed_ruleset(details: Mapping[str, object] | None, run_sha256: str | None) -> str | None:
    """The rule configuration a finding was observed under: its rule's own hash when the engine
    reports one (``details.rule_sha256``), else the run's rule-set hash."""
    value = (details or {}).get("rule_sha256")
    return value if isinstance(value, str) else run_sha256


@dataclass(frozen=True, slots=True)
class Prior:
    """The earlier observation being rechecked."""

    engine: str
    rule_id: str
    path: str
    engine_version: str | None
    ruleset_sha256: str | None


@dataclass(frozen=True, slots=True)
class Recheck:
    state: RecheckState
    reason: str


def classify_absence(
    prior: Prior,
    run: RunView | None,
    *,
    file_present: bool,
    file_outcome: str | None,
) -> Recheck:
    """``file_present``: the path is an analyzable entry of the newer snapshot.
    ``file_outcome``: the newer engine run's coverage outcome for that file (None if none)."""
    if run is None:
        return Recheck(RecheckState.NOT_RECHECKED, f"{prior.engine} did not run in this scan")
    if run.state not in _COMPLETED:
        return Recheck(RecheckState.NOT_RECHECKED, f"{prior.engine} did not complete ({run.state})")
    if not file_present:
        return Recheck(
            RecheckState.UNKNOWN,
            f"{prior.path} is not an analyzable file in this snapshot (deleted, renamed, "
            "excluded or changed type); absence cannot be verified",
        )
    if run.enabled_rules is not None and prior.rule_id not in run.enabled_rules:
        return Recheck(RecheckState.RULE_OBSOLETE, f"rule {prior.rule_id} is no longer enabled")
    if file_outcome != CoverageOutcome.ANALYZED.value:
        return Recheck(
            RecheckState.NOT_RECHECKED,
            f"{prior.path} was not analyzed by {prior.engine} ({file_outcome or 'no outcome'})",
        )
    if prior.engine_version != run.engine_version:
        return Recheck(
            RecheckState.UNKNOWN,
            f"{prior.engine} version changed ({prior.engine_version} -> {run.engine_version})",
        )
    if run.rule_hashes is not None:
        if prior.ruleset_sha256 != run.rule_hashes.get(prior.rule_id):
            return Recheck(
                RecheckState.UNKNOWN,
                f"rule {prior.rule_id} changed since the earlier observation",
            )
    elif prior.ruleset_sha256 != run.ruleset_sha256:
        return Recheck(
            RecheckState.UNKNOWN,
            f"{prior.engine} rules/configuration changed since the earlier observation",
        )
    return Recheck(
        RecheckState.VERIFIED_ABSENT,
        f"compatible {prior.engine} {run.engine_version} run analyzed {prior.path} "
        "without reporting it",
    )
