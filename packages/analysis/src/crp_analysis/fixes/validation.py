"""Validation ladder for one fix proposal, run on copies of the file (never the upload).

1. Integrity: the upload's file is the one the fix was prepared for (stale base otherwise), the
   edits apply without conflict, the result and patch hashes match the proposal, and the policy
   allows the change (scope, no suppressions, no weakened tests, size).
2. Syntax: the changed file still parses (Tree-sitter, secure XML, JSON; Apex through PMD).
3. Checks: the platform's own trusted engines run on the original and the changed copy; the
   original finding must be gone and no check may report more problems than before.
4. Tests and 5. build: not run. Running a project's tests or build executes uploaded code, which
   needs an isolated runner (and, for SAP/Salesforce, a licensed build or an authorized org).

Every result is bound to the exact patch and result hashes. Model or test agreement is never a
substitute for these checks.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from crp_analysis import structure
from crp_analysis.engines.base import CancelToken, EngineAdapter, EngineOutcome
from crp_analysis.fixes import policy
from crp_analysis.fixes.patching import Edit, PatchError, apply_edits, sha256_text, unified_diff
from crp_analysis.frameworks import xmlsafe
from crp_analysis.normalize import normalize
from crp_analysis.workspace import written

PASSED, FAILED, NOT_RUN = "passed", "failed", "not_run"
SKIPPED = "Not run because an earlier step failed."
SOURCE_ONLY = "Source-level checks only: not compiled, built or tested."
_NO_RUNNER = (
    "Running the project's tests would execute uploaded code; this deployment has no isolated "
    "runner for it."
)
_BUILD = {
    "sap": "An SAP Commerce build needs a customer-licensed distribution in an authorized, "
    "isolated environment (see the SAP build validation profile).",
    "salesforce": "A check-only deployment and Apex tests need an authorized Salesforce org "
    "(see the Salesforce org validation profile).",
}
_DEFAULT_BUILD = (
    "Compiling needs the project's build toolchain and dependencies in an isolated runner, "
    "which this deployment does not have."
)


@dataclass(slots=True)
class Step:
    id: str
    label: str
    state: str
    detail: str


@dataclass(frozen=True, slots=True)
class TargetFinding:
    engine: str
    rule_id: str
    start_line: int | None


@dataclass(frozen=True, slots=True)
class LadderInput:
    path: str
    language: str | None
    base_text: str
    edits: tuple[Edit, ...]
    base_sha256: str
    result_sha256: str
    patch_sha256: str
    allowed_paths: frozenset[str]
    finding: TargetFinding
    platform: str | None = None  # "sap" | "salesforce" | None


@dataclass(slots=True)
class LadderResult:
    steps: list[Step] = field(default_factory=list)
    passed: bool = False
    summary: str = ""
    canceled: bool = False

    def steps_json(self) -> list[dict[str, str]]:
        return [asdict(step) for step in self.steps]


@dataclass(slots=True)
class _Run:
    engine: str
    base: EngineOutcome
    patched: EngineOutcome
    base_found: Counter[str]
    patched_found: Counter[str]
    target_lines: list[int]  # lines of the target rule in the patched copy


def _parse(path: str, language: str | None, text: str) -> str | None:
    """Problem description if the text does not parse, else None; "" when no parser applies."""
    if language in {"java", "javascript", "typescript"}:
        result = structure.extract(text.encode("utf-8"), path, language, max_symbols=10_000)
        if result.status in {"FAILED", "PARTIAL"}:
            return f"syntax errors ({result.error_count})"
        return None
    if path.endswith(".xml"):
        try:
            xmlsafe.parse(text)
        except ValueError as exc:
            return str(exc)
        return None
    if path.endswith(".json"):
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return f"invalid JSON ({exc.msg})"
        return None
    return ""


def _engines(
    inp: LadderInput,
    after: str,
    adapters: dict[str, EngineAdapter],
    work: Path,
    cancel: CancelToken,
) -> tuple[list[_Run], list[str]]:
    runs: list[_Run] = []
    unavailable: list[str] = []
    for name, adapter in adapters.items():
        if cancel.cancelled or not adapter.is_eligible(inp.path, inp.language):
            continue
        availability = adapter.availability()
        if not availability.available:
            unavailable.append(f"{name}: {availability.reason}")
            continue
        outcomes: list[tuple[EngineOutcome, Counter[str], list[int]]] = []
        for label, text in (("base", inp.base_text), ("patched", after)):
            with written(work / f"{name}-{label}", {inp.path: text.encode("utf-8")}) as root:
                outcome = adapter.run(root, [inp.path], cancel=cancel, heartbeat=lambda _m: None)
                found = normalize(name, root, outcome.findings)
            counts: Counter[str] = Counter(f.raw.rule_id for f in found)
            lines = [
                f.raw.start_line or 0
                for f in found
                if name == inp.finding.engine and f.raw.rule_id == inp.finding.rule_id
            ]
            outcomes.append((outcome, counts, lines))
        (base, base_found, base_lines), (patched, patched_found, patched_lines) = outcomes
        if name == inp.finding.engine and inp.finding.start_line not in base_lines:
            base_found["__target_not_reproduced__"] = 1
        runs.append(_Run(name, base, patched, base_found, patched_found, patched_lines))
    return runs, unavailable


def _changed_region(edits: tuple[Edit, ...]) -> set[int]:
    """Lines of the patched file produced by the edits (edits applied in order)."""
    region: set[int] = set()
    shift = 0
    for edit in sorted(edits, key=lambda e: e.start_line):
        start = edit.start_line + shift
        region.update(range(start, start + max(len(edit.replacement), 1)))
        shift += len(edit.replacement) - len(edit.original)
    return region


def run_ladder(
    inp: LadderInput,
    adapters: dict[str, EngineAdapter],
    work: Path,
    *,
    cancel: CancelToken,
    progress: Callable[[str], None] = lambda _m: None,
) -> LadderResult:
    result = LadderResult()
    # 1. integrity
    progress("checking the patch")
    problems: list[str] = []
    after = ""
    if sha256_text(inp.base_text) != inp.base_sha256:
        problems.append(
            "stale base: the file in this upload is not the one the fix was prepared for"
        )
    else:
        try:
            after = apply_edits(inp.base_text, list(inp.edits))
        except PatchError as exc:
            problems.append(f"{exc.code}: {exc}")
    if not problems:
        if sha256_text(after) != inp.result_sha256:
            problems.append("the changed file does not match the proposal's result hash")
        if sha256_text(unified_diff(inp.path, inp.base_text, after)) != inp.patch_sha256:
            problems.append("the patch does not match the proposal's patch hash")
        problems.extend(
            f"{v.code}: {v.message}"
            for v in policy.check(inp.path, inp.base_text, after, inp.allowed_paths)
        )
    result.steps.append(
        Step(
            "integrity",
            "Patch applies to this upload",
            FAILED if problems else PASSED,
            "; ".join(problems)
            or "The file matches the fix's base, the edits apply cleanly and stay in scope.",
        )
    )
    if problems:
        result.steps.extend(_skipped(inp))
        result.summary = f"The patch cannot be used: {problems[0]}"
        return result

    # 2-3. syntax and trusted checks on copies
    progress("running checks on the changed copy")
    runs, unavailable = _engines(inp, after, adapters, work, cancel)
    if cancel.cancelled:
        result.canceled = True
        result.summary = "Validation was canceled."
        return result
    syntax_problem = _parse(inp.path, inp.language, after)
    base_problem = _parse(inp.path, inp.language, inp.base_text)
    if syntax_problem == "":  # no parser here: rely on the engines' own parsers (e.g. Apex/PMD)
        failed_parse = [r.engine for r in runs if r.patched.problems and not r.base.problems]
        syntax = Step(
            "syntax",
            "Changed file still parses",
            FAILED if failed_parse else (PASSED if runs else NOT_RUN),
            f"{', '.join(failed_parse)} could not read the changed file"
            if failed_parse
            else (
                "Checked by the analyzers' own parsers."
                if runs
                else "No parser is available for this file type."
            ),
        )
    else:
        broken = syntax_problem is not None and base_problem is None
        syntax = Step(
            "syntax",
            "Changed file still parses",
            FAILED if broken else PASSED,
            f"The change introduced {syntax_problem}." if broken else "The changed file parses.",
        )
    result.steps.append(syntax)
    result.steps.append(_checks(inp, runs, unavailable))
    # 4-5. execution-based steps: not run (no isolated runner / environment)
    result.steps.append(Step("tests", "Project tests", NOT_RUN, _NO_RUNNER))
    result.steps.append(
        Step(
            "build", "Build or deployment", NOT_RUN, _BUILD.get(inp.platform or "", _DEFAULT_BUILD)
        )
    )
    failed = [s for s in result.steps if s.state == FAILED]
    checks = next(s for s in result.steps if s.id == "checks")
    result.passed = not failed and checks.state == PASSED
    if result.passed:
        result.summary = (
            "Source checks passed: the fix applies cleanly to this upload, the file parses, the "
            f"check no longer reports the problem and no new problems appeared. {SOURCE_ONLY}"
        )
    elif failed:
        result.summary = f"{failed[0].label}: {failed[0].detail}"
    else:
        result.summary = f"{checks.label}: {checks.detail}"
    return result


def _checks(inp: LadderInput, runs: list[_Run], unavailable: list[str]) -> Step:
    label = "Checks no longer report the problem"
    target = next((r for r in runs if r.engine == inp.finding.engine), None)
    if target is None:
        detail = "The check that reported the problem is not available here"
        if unavailable:
            detail += f" ({'; '.join(unavailable)})"
        return Step("checks", label, NOT_RUN, detail + ".")
    if target.base_found.get("__target_not_reproduced__"):
        return Step(
            "checks",
            label,
            FAILED,
            "The check did not reproduce the original finding on this file alone, so the fix "
            "cannot be confirmed by it.",
        )
    region = _changed_region(inp.edits)
    still = [line for line in target.target_lines if line in region]
    if still:
        return Step(
            "checks",
            label,
            FAILED,
            f"{inp.finding.rule_id} is still reported on line {still[0]} of the changed file.",
        )
    new: list[str] = []
    for run in runs:
        for rule, count in run.patched_found.items():
            before = run.base_found.get(rule, 0)
            if count > before and not (
                run.engine == inp.finding.engine and rule == inp.finding.rule_id
            ):
                new.append(f"{run.engine} {rule} (+{count - before})")
    if new:
        return Step(
            "checks",
            label,
            FAILED,
            f"The change introduces new findings: {', '.join(sorted(new)[:5])}.",
        )
    engines = ", ".join(sorted(r.engine for r in runs))
    detail = f"Re-checked with {engines}: the finding is gone and nothing new was reported."
    if unavailable:
        detail += f" Not available: {'; '.join(unavailable)}."
    return Step("checks", label, PASSED, detail)


def _skipped(inp: LadderInput) -> list[Step]:
    return [
        Step("syntax", "Changed file still parses", NOT_RUN, SKIPPED),
        Step("checks", "Checks no longer report the problem", NOT_RUN, SKIPPED),
        Step("tests", "Project tests", NOT_RUN, _NO_RUNNER),
        Step(
            "build",
            "Build or deployment",
            NOT_RUN,
            _BUILD.get(inp.platform or "", _DEFAULT_BUILD),
        ),
    ]
