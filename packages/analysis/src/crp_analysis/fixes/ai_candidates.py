"""AI fix candidates (P08): turn a model's submitted edits into checked, labelled candidates.

Model output is untrusted (the code it read may contain instructions). Every candidate passes the
same gates as a recipe fix before anyone can apply it:
- its edits must match the current file: exactly at the stated lines, or (models often miscount
  lines or re-indent what they copy) the same lines found once nearby, or lines that differ only
  in indentation or trailing spaces, found once nearby. Spacing inside a line must match (it can
  be part of a string). The patch is then rebuilt from the file's real lines and indentation, so
  every later check sees exact text, and the candidate says what was adjusted;
- the strict change policy (one file, no suppressions, no weakened, skipped or focused tests, no
  analyzer or build configuration, size limit);
- the P05 validation ladder on copies: the file still parses, the check that reported the finding
  no longer reports it there, and no check reports anything new.

Duplicates and no-op candidates are dropped. Nothing here changes a workspace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crp_analysis.ai.results import SubmittedEdit, SubmittedFixes
from crp_analysis.engines.base import CancelToken, EngineAdapter
from crp_analysis.fixes.patching import (
    Edit,
    PatchError,
    apply_edits,
    changed_lines,
    relocate,
    sha256_text,
    unified_diff,
)
from crp_analysis.fixes.validation import (
    FAILED,
    PASSED,
    LadderInput,
    TargetFinding,
    run_ladder,
)

LABEL = "AI suggestion: review it before applying. Not compiled, built or tested."
_NUMBERED = re.compile(r"^\s*(\d+) \| ?(.*)$")


@dataclass(slots=True)
class CheckedCandidate:
    index: int
    title: str
    explanation: str
    behaviour_note: str
    confidence: str
    edits: list[dict[str, object]] = field(default_factory=list)
    patch: str | None = None
    result_sha256: str | None = None
    patch_sha256: str | None = None
    changed_lines: int = 0
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    steps: list[dict[str, str]] = field(default_factory=list)
    passed: bool = False
    summary: str = ""
    applicable: bool = False
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "explanation": self.explanation,
            "behaviour_note": self.behaviour_note,
            "confidence": self.confidence,
            "edits": self.edits,
            "patch": self.patch,
            "result_sha256": self.result_sha256,
            "patch_sha256": self.patch_sha256,
            "changed_lines": self.changed_lines,
            "problems": self.problems,
            "notes": self.notes,
            "steps": self.steps,
            "passed": self.passed,
            "summary": self.summary,
            "applicable": self.applicable,
            "reason": self.reason,
            "label": LABEL,
            "applied_at": None,
        }


def locate_line(base_text: str | None, current: str, line: int | None) -> int | None:
    """Where the finding's line is in the workspace's current text (unchanged files: same line)."""
    if line is None or base_text is None or base_text == current:
        return line
    base_lines = base_text.splitlines()
    if not 1 <= line <= len(base_lines):
        return line
    probe = Edit("", line, line, (base_lines[line - 1],), (base_lines[line - 1],))
    try:
        return relocate(current, probe, window=max(50, len(current.splitlines()))).start_line
    except PatchError:
        return line


def _clean(lines: list[str], first: int) -> tuple[str, ...]:
    """Drop stray CRs, and line-number prefixes copied from the prompt when they are exactly the
    expected consecutive numbers (a deterministic, verifiable normalisation)."""
    stripped = [line.rstrip("\r") for line in lines]
    matches = [_NUMBERED.match(line) for line in stripped]
    if (
        stripped
        and all(matches)
        and [int(m.group(1)) for m in matches if m] == list(range(first, first + len(stripped)))
    ):
        return tuple(m.group(2) for m in matches if m)
    return tuple(stripped)


def _edit(path: str, submitted: SubmittedEdit) -> Edit:
    original = _clean(submitted.original, submitted.start_line)
    return Edit(
        path,
        submitted.start_line,
        submitted.end_line,
        original,
        tuple(line.rstrip("\r") for line in submitted.replacement),
    )


MATCH_WINDOW = 40  # lines around the stated position searched for the model's lines


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _reindent(lines: tuple[str, ...], model: str, actual: str) -> tuple[str, ...]:
    """Replace the model's base indentation with the file's in replacement lines."""
    if model == actual:
        return lines
    return tuple(
        actual + line[len(model) :] if line.startswith(model) and line.strip() else line
        for line in lines
    )


def resolve_edit(text: str, edit: Edit, window: int = MATCH_WINDOW) -> tuple[Edit, str | None]:
    """The edit placed on the file's real lines, and a note when it had to be adjusted.

    Raises PatchError when the lines are not found, or found more than once, nearby."""
    lines = text.splitlines()
    size = len(edit.original)
    stated = tuple(lines[edit.start_line - 1 : edit.start_line - 1 + size])
    if size == 0 or stated == edit.original:
        return edit, None  # exact (an insertion's range is checked by apply_edits)
    try:
        moved: Edit | None = relocate(text, edit, window=window)
    except PatchError:
        moved = None
    if moved is not None:
        note = f"The suggestion named line {edit.start_line}; the lines are at {moved.start_line}."
        return moved, note
    wanted = tuple(line.strip() for line in edit.original)
    low = max(0, edit.start_line - 1 - window)
    high = min(len(lines) - size, edit.start_line - 1 + window)
    starts = [
        i
        for i in range(low, high + 1)
        if tuple(line.strip() for line in lines[i : i + size]) == wanted
    ]
    if len(starts) != 1:
        raise PatchError(
            "conflict", "the lines to replace are not in the file (or appear more than once)"
        )
    start = starts[0]
    actual = tuple(lines[start : start + size])
    first = next((n for n, line in enumerate(edit.original) if line.strip()), 0)
    replacement = _reindent(edit.replacement, _indent(edit.original[first]), _indent(actual[first]))
    adjusted = Edit(edit.path, start + 1, start + size, actual, replacement)
    return (
        adjusted,
        "The suggestion's indentation or trailing spaces differed from the file; it was "
        "matched to the file's lines.",
    )


def check_candidates(
    *,
    path: str,
    language: str | None,
    current: str,
    submitted: SubmittedFixes,
    finding: TargetFinding,
    adapters: dict[str, EngineAdapter],
    work: Path,
    cancel: CancelToken,
    platform: str | None,
    max_candidates: int,
) -> list[CheckedCandidate]:
    base_sha = sha256_text(current)
    allowed = frozenset({path})
    seen: set[str] = set()
    checked: list[CheckedCandidate] = []
    for proposal in submitted.candidates:
        if len(checked) >= max_candidates or cancel.cancelled:
            break
        candidate = CheckedCandidate(
            index=len(checked),
            title=proposal.title.strip(),
            explanation=proposal.explanation.strip(),
            behaviour_note=proposal.behaviour_note.strip(),
            confidence=proposal.confidence.value,
        )
        edits = tuple(_edit(path, e) for e in proposal.edits)
        try:
            resolved = [resolve_edit(current, e) for e in edits]
            edits = tuple(edit for edit, _ in resolved)
            candidate.notes = [note for _, note in resolved if note]
            candidate.edits = [e.to_json() for e in edits]
            after = apply_edits(current, list(edits))
        except PatchError as exc:
            candidate.problems.append(f"The edits do not match the file: {exc}")
            candidate.reason = "The suggested lines do not match the file; nothing was checked."
            candidate.summary = candidate.reason
            checked.append(candidate)
            continue
        if after == current:
            continue  # a no-op is not a candidate
        result_sha = sha256_text(after)
        if result_sha in seen:
            continue
        seen.add(result_sha)
        patch = unified_diff(path, current, after)
        candidate.patch = patch
        candidate.result_sha256 = result_sha
        candidate.patch_sha256 = sha256_text(patch)
        candidate.changed_lines = changed_lines(current, after)
        result = run_ladder(
            LadderInput(
                path=path,
                language=language,
                base_text=current,
                edits=edits,
                base_sha256=base_sha,
                result_sha256=result_sha,
                patch_sha256=candidate.patch_sha256,
                allowed_paths=allowed,
                finding=finding,
                platform=platform,
            ),
            adapters,
            work / f"candidate-{candidate.index}",
            cancel=cancel,
        )
        candidate.steps = result.steps_json()
        candidate.passed = result.passed
        candidate.summary = result.summary
        states = {step.id: step.state for step in result.steps}
        blocking = [
            step
            for step in result.steps
            if step.id in {"integrity", "syntax", "checks"} and step.state == FAILED
        ]
        if result.canceled:
            candidate.reason = "Checking was stopped."
        elif states.get("integrity") != PASSED:
            candidate.problems.append(next(s.detail for s in result.steps if s.id == "integrity"))
            candidate.reason = "Refused by the change policy: " + candidate.problems[-1]
        elif blocking:
            candidate.reason = f"{blocking[0].label}: {blocking[0].detail}"
        else:
            candidate.applicable = True
        checked.append(candidate)
    return checked
