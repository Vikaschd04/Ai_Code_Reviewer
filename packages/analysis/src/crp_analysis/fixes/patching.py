"""Line-based edits, their application to a copy of a file, and git-compatible unified diffs.

An edit names the exact original lines it replaces; applying it to text whose lines differ is a
conflict, never a guess. Line endings and a missing final newline are preserved. Nothing here
writes to the upload: callers apply edits to text and store the result separately.
"""

from __future__ import annotations

import difflib
import hashlib
import itertools
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_EDIT_LINES = 400


class PatchError(ValueError):
    """An edit cannot be applied; ``code`` is a stable machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Edit:
    """Replace ``original`` (lines ``start_line``..``end_line``, 1-based) with ``replacement``."""

    path: str
    start_line: int
    end_line: int
    original: tuple[str, ...]
    replacement: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "original": list(self.original),
            "replacement": list(self.replacement),
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> Edit:
        original, replacement = data["original"], data["replacement"]
        if not isinstance(original, list) or not isinstance(replacement, list):
            raise PatchError("invalid_edit", "edit lines must be lists")
        return cls(
            str(data["path"]),
            int(str(data["start_line"])),
            int(str(data["end_line"])),
            tuple(str(line) for line in original),
            tuple(str(line) for line in replacement),
        )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return bool(path) and not pure.is_absolute() and ".." not in pure.parts and "\\" not in path


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _split(text: str) -> tuple[list[str], bool]:
    """Lines without terminators, and whether the text ends with a newline."""
    return text.splitlines(), text.endswith(("\n", "\r"))


def apply_edits(text: str, edits: list[Edit]) -> str:
    """Apply non-overlapping edits of one file to ``text``; raise PatchError on any mismatch."""
    lines, final_newline = _split(text)
    ordered = sorted(edits, key=lambda e: e.start_line)
    for previous, current in itertools.pairwise(ordered):
        if current.start_line <= previous.end_line:
            raise PatchError("overlapping_edits", "edits overlap")
    for edit in reversed(ordered):
        if edit.start_line < 1 or edit.end_line < edit.start_line - 1:
            raise PatchError("invalid_edit", "edit line range is invalid")
        if len(edit.original) + len(edit.replacement) > MAX_EDIT_LINES:
            raise PatchError("edit_too_large", "edit exceeds the line budget")
        span = edit.end_line - edit.start_line + 1
        if span != len(edit.original) or edit.end_line > len(lines):
            raise PatchError("conflict", "edit range does not match the file")
        if tuple(lines[edit.start_line - 1 : edit.end_line]) != edit.original:
            raise PatchError("conflict", "the lines to replace are not in the file as expected")
        lines[edit.start_line - 1 : edit.end_line] = list(edit.replacement)
    joined = _newline(text).join(lines)
    return joined + (_newline(text) if final_newline and lines else "")


def relocate(text: str, edit: Edit, window: int = 50) -> Edit:
    """The same edit at the position where its original lines now appear (for a newer copy of
    the file). Raises PatchError("conflict") when they are absent or appear more than once."""
    lines, _ = _split(text)
    size = len(edit.original)
    starts = [
        i + 1
        for i in range(
            max(0, edit.start_line - 1 - window), min(len(lines), edit.start_line + window)
        )
        if tuple(lines[i : i + size]) == edit.original
    ]
    if len(starts) != 1:
        raise PatchError(
            "conflict",
            "the changed lines are missing or ambiguous in the other version of the file",
        )
    return Edit(edit.path, starts[0], starts[0] + size - 1, edit.original, edit.replacement)


def unified_diff(path: str, before: str, after: str, context: int = 3) -> str:
    """A diff ``git apply -p1`` and ``patch -p1`` accept, including missing final newlines."""
    old = before.splitlines(keepends=True)
    new = after.splitlines(keepends=True)
    out: list[str] = [f"diff --git a/{path} b/{path}\n"]
    for line in difflib.unified_diff(old, new, f"a/{path}", f"b/{path}", n=context):
        if line.endswith(("\n", "\r")):
            out.append(line)
        else:
            out.append(line + "\n\\ No newline at end of file\n")
    return "".join(out) if len(out) > 1 else ""


def changed_lines(before: str, after: str) -> int:
    matcher = difflib.SequenceMatcher(a=before.splitlines(), b=after.splitlines(), autojunk=False)
    return sum(
        max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"
    )
