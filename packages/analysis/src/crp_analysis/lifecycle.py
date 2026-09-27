"""Strict recheck classification for a previously observed finding that is now absent.

An absent finding is only VERIFIED_ABSENT when the newer scan could actually have reported it:
the same engine completed, analyzed the same path, the rule is still enabled, and the engine
version and rule-set hash equal those of the earlier observation. Anything else is NOT_RECHECKED,
UNKNOWN or RULE_OBSOLETE - never "fixed".
"""

from __future__ import annotations

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
    if prior.ruleset_sha256 != run.ruleset_sha256:
        return Recheck(
            RecheckState.UNKNOWN,
            f"{prior.engine} rules/configuration changed since the earlier observation",
        )
    return Recheck(
        RecheckState.VERIFIED_ABSENT,
        f"compatible {prior.engine} {run.engine_version} run analyzed {prior.path} "
        "without reporting it",
    )
