"""Canonical path policy for submitted entries.

Paths are relative POSIX strings, NFC-normalized. Traversal, absolute, drive/UNC and backslash
paths, control characters and empty or dot segments are rejected. Collisions are detected on the
case-folded NFC form so that entries which would overwrite each other on common filesystems are
refused instead of silently merged.
"""

from __future__ import annotations

import re
import unicodedata

_DRIVE = re.compile(r"^[A-Za-z]:")


class PathRejectedError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def safe_display(raw: str, limit: int = 160) -> str:
    """Escape a submitted name for logs/UI: control characters become \\xNN, length bounded."""
    escaped = "".join(
        ch if ch.isprintable() and ch not in "\r\n\t" else f"\\x{ord(ch):02x}" for ch in raw
    )
    return escaped if len(escaped) <= limit else escaped[: limit - 1] + "…"


def canonical_path(raw: str, *, max_length: int, max_depth: int) -> str:
    """Validate and normalize one entry name (without trailing slash)."""
    if not raw:
        raise PathRejectedError("empty_path", "entry has an empty name")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise PathRejectedError("control_character", "entry name contains control characters")
    if "\\" in raw:
        raise PathRejectedError("windows_path", "entry name uses backslashes")
    if raw.startswith("/"):
        raise PathRejectedError("absolute_path", "entry name is absolute")
    if _DRIVE.match(raw):
        raise PathRejectedError("windows_drive_path", "entry name contains a drive letter")
    segments = raw.split("/")
    for segment in segments:
        if segment == "..":
            raise PathRejectedError("path_traversal", "entry name contains '..'")
        if segment in {"", "."}:
            raise PathRejectedError("invalid_segment", "entry name has empty or '.' segments")
    normalized = unicodedata.normalize("NFC", raw)
    if len(normalized.encode("utf-8")) > max_length:
        raise PathRejectedError("path_too_long", f"entry name exceeds {max_length} bytes")
    if len(segments) > max_depth:
        raise PathRejectedError("path_too_deep", f"entry is nested deeper than {max_depth} levels")
    return normalized


def collision_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


class CollisionTracker:
    """Detect duplicate/case/Unicode collisions and file-vs-directory conflicts."""

    def __init__(self) -> None:
        self._files: dict[str, str] = {}
        self._dirs: set[str] = set()

    def add(self, path: str) -> str | None:
        """Register a file path; returns the conflicting earlier path, if any."""
        key = collision_key(path)
        if key in self._files:
            return self._files[key]
        if key in self._dirs:
            return path + "/"
        parts = key.split("/")
        for index in range(1, len(parts)):
            prefix = "/".join(parts[:index])
            if prefix in self._files:
                return self._files[prefix]
            self._dirs.add(prefix)
        self._files[key] = path
        return None
