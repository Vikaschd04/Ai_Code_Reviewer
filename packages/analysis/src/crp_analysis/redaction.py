"""Best-effort redaction of likely secret values in source excerpts shown to users.

This is a display safeguard, not a secret scanner: it masks well-known token formats and literal
values assigned to secret-looking names. Dedicated secret scanning arrives with later engines.
"""

from __future__ import annotations

import re

_MASK = "«redacted»"
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*"), "-----BEGIN PRIVATE KEY----- " + _MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _MASK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), _MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), _MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), _MASK),
    (
        re.compile(r"((?:SecretKeySpec|IvParameterSpec|PBEKeySpec)\(\s*)\"[^\"]+\""),
        r'\1"' + _MASK + '"',
    ),
    (
        re.compile(
            r"(?i)\b([a-z0-9_]*(?:password|passwd|secret|api[_-]?key|access[_-]?key|token|"
            r"private[_-]?key)[a-z0-9_]*\s*[:=]\s*)([\"'])[^\"'\s]{4,}\2"
        ),
        r"\1\2" + _MASK + r"\2",
    ),
)


def redact_line(line: str) -> tuple[str, int]:
    count = 0
    for pattern, replacement in _PATTERNS:
        line, hits = pattern.subn(replacement, line)
        count += hits
    return line, count


def redact_lines(lines: list[str]) -> tuple[list[str], int]:
    total = 0
    output = []
    for line in lines:
        redacted, hits = redact_line(line)
        total += hits
        output.append(redacted)
    return output, total
