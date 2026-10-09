"""Normalize raw engine findings: validate spans, fingerprint, classify and correlate duplicates.

Fingerprint (identity of one engine observation): engine + rule + path + normalized source text at
the span start (or a stable engine identity such as ``package@version`` for dependency findings)
+ occurrence index. Line numbers alone are never the identity.

Correlation key (cross-engine duplicate family): rule family + path + start line + the finding's
occurrence index for that family on that line *within its engine*. Two engines reporting the same
construct correlate even if their columns differ, while two distinct occurrences on one line, or
the same family on different lines, never collapse. Findings without a family correlate only with
themselves. Correlation groups findings for display; every engine observation is preserved.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from crp_analysis.catalog import catalog_version, lookup
from crp_analysis.engines.base import Guidance, RawFinding

FINGERPRINT_VERSION = "crp-fp-v1"
CORRELATION_VERSION = "crp-corr-v1"


def normalization_identity() -> str:
    """Versions that change normalized output; part of every engine cache key."""
    return f"{FINGERPRINT_VERSION}|{CORRELATION_VERSION}|{catalog_version()}"


_WS = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class NormalizedFinding:
    raw: RawFinding
    fingerprint: str
    correlation_key: str
    family: str | None
    title: str
    category: str
    severity: str
    in_catalog: bool
    rule_url: str | None
    start_line: int | None
    end_line: int | None
    anchor: str


def _line_text(lines: list[str], number: int) -> str:
    if 1 <= number <= len(lines):
        return _WS.sub(" ", lines[number - 1]).strip()
    return ""


def evidence_line(text: str, line: int) -> str:
    """The normalized text of one line, as used in fingerprints (for rename-aware matching)."""
    return _line_text(text.splitlines(), line)


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def normalize(engine: str, root: Path, findings: list[RawFinding]) -> list[NormalizedFinding]:
    cache: dict[str, list[str]] = {}
    occurrences: Counter[tuple[str, str, str]] = Counter()
    family_counts: Counter[tuple[str, str, int]] = Counter()
    result: list[NormalizedFinding] = []
    ordered = sorted(
        findings, key=lambda f: (f.path, f.start_line or 0, f.start_column or 0, f.rule_id)
    )
    for raw in ordered:
        if raw.path not in cache:
            try:
                text = (root / raw.path).read_text(encoding="utf-8", errors="replace")
                cache[raw.path] = text.splitlines()
            except OSError:
                cache[raw.path] = []
        lines = cache[raw.path]
        start: int | None = None
        end: int | None = None
        if raw.start_line is not None:
            max_line = max(len(lines), 1)
            start = min(max(raw.start_line, 1), max_line)
            end = min(max(raw.end_line or start, start), max_line)
        anchor = raw.anchor if start is not None or raw.anchor != "source_span" else "file"
        text = (
            raw.identity
            if raw.identity is not None
            else (_line_text(lines, start) if start else "")
        )
        key = (raw.rule_id, raw.path, text)
        occurrence = occurrences[key]
        occurrences[key] += 1
        fingerprint = _digest(
            FINGERPRINT_VERSION, engine, raw.rule_id, raw.path, text, str(occurrence)
        )
        info = lookup(engine, raw.rule_id, raw.engine_severity, raw.rule_url)
        family = info.family
        if family is not None and start is not None:
            slot = family_counts[(family, raw.path, start)]
            family_counts[(family, raw.path, start)] += 1
            correlation = _digest(CORRELATION_VERSION, family, raw.path, str(start), str(slot))
        else:
            correlation = fingerprint
        guidance = raw.guidance
        result.append(
            NormalizedFinding(
                raw=raw,
                fingerprint=fingerprint,
                correlation_key=correlation,
                family=family,
                title=raw.title
                or (guidance.title if guidance and not info.in_catalog else info.title),
                category=raw.category or info.category.value,
                severity=raw.severity or info.severity.value,
                in_catalog=info.in_catalog or guidance is not None,
                rule_url=(guidance.url if guidance else None) or info.url or raw.rule_url,
                start_line=start,
                end_line=end,
                anchor=anchor,
            )
        )
    return result


def to_payload(finding: NormalizedFinding) -> dict[str, Any]:
    """JSON-safe form for the per-file engine cache (no source text is included)."""
    data = asdict(finding)
    data["raw"] = asdict(finding.raw)
    return data


def from_payload(data: dict[str, Any]) -> NormalizedFinding:
    raw = dict(data["raw"])
    guidance = raw.pop("guidance", None)
    fields = {k: v for k, v in data.items() if k != "raw"}
    return NormalizedFinding(
        raw=RawFinding(**raw, guidance=Guidance(**guidance) if guidance else None), **fields
    )
