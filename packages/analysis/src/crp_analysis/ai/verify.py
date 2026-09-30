"""Deterministic verification of AI citations against the frozen snapshot.

A claim is only as good as its anchors: the file must be a reviewable file of the run's
snapshot, the line range must exist, and the quoted code must match those lines (compared after
the same secret masking the model saw, whitespace-insensitive, allowing two lines of drift).
Model agreement is never treated as verification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from crp_analysis.ai.results import Anchor
from crp_analysis.ai.snapshot import SnapshotReader
from crp_analysis.redaction import redact_line
from crp_core.domain.states import AiEvidenceClass

_LINE_PREFIX = re.compile(r"^\s*\d+\s*\|\s?", re.MULTILINE)
_SPACE = re.compile(r"\s+")
_DRIFT = 2


class AnchorStatus(StrEnum):
    VERIFIED = "verified"
    UNQUOTED = "unquoted"  # location exists, but no quote was given to check
    QUOTE_MISMATCH = "quote_mismatch"
    BAD_RANGE = "bad_range"
    UNKNOWN_PATH = "unknown_path"


@dataclass(frozen=True, slots=True)
class AnchorCheck:
    path: str
    start_line: int
    end_line: int
    status: AnchorStatus
    sha256: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "status": self.status.value,
            "sha256": self.sha256,
        }


def _normalize(text: str) -> str:
    text = text.replace("​", "")
    text = _LINE_PREFIX.sub("", text)
    return _SPACE.sub(" ", text).strip()


async def check_anchor(reader: SnapshotReader, anchor: Anchor) -> AnchorCheck:
    files = await reader.files()
    info = files.get(anchor.path)
    if info is None:
        return AnchorCheck(
            anchor.path, anchor.start_line, anchor.end_line, AnchorStatus.UNKNOWN_PATH, None
        )
    lines = await reader.lines(anchor.path)
    if anchor.end_line < anchor.start_line or anchor.end_line > len(lines):
        return AnchorCheck(
            anchor.path, anchor.start_line, anchor.end_line, AnchorStatus.BAD_RANGE, info.sha256
        )
    quote = _normalize(anchor.quote)
    if not quote:
        return AnchorCheck(
            anchor.path, anchor.start_line, anchor.end_line, AnchorStatus.UNQUOTED, info.sha256
        )
    low = max(1, anchor.start_line - _DRIFT)
    high = min(len(lines), anchor.end_line + _DRIFT)
    window = _normalize(" ".join(redact_line(line)[0] for line in lines[low - 1 : high]))
    raw_window = _normalize(" ".join(lines[low - 1 : high]))
    status = (
        AnchorStatus.VERIFIED
        if quote in window or quote in raw_window
        else AnchorStatus.QUOTE_MISMATCH
    )
    return AnchorCheck(anchor.path, anchor.start_line, anchor.end_line, status, info.sha256)


async def check_anchors(reader: SnapshotReader, anchors: list[Anchor]) -> list[AnchorCheck]:
    return [await check_anchor(reader, anchor) for anchor in anchors]


def evidence_class(checks: list[AnchorCheck]) -> AiEvidenceClass:
    """VERIFIED only when every anchor is verified; REJECTED when none points at real code."""
    if not checks:
        return AiEvidenceClass.HYPOTHESIS
    if all(
        c.status in {AnchorStatus.UNKNOWN_PATH, AnchorStatus.BAD_RANGE, AnchorStatus.QUOTE_MISMATCH}
        for c in checks
    ):
        return AiEvidenceClass.REJECTED
    if all(c.status is AnchorStatus.VERIFIED for c in checks):
        return AiEvidenceClass.VERIFIED_ANCHOR
    return AiEvidenceClass.HYPOTHESIS
