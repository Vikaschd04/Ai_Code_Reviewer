"""Fix workspaces (P08): many file revisions on top of one upload, kept apart from the upload.

Pure helpers shared by the API and the worker:
- a content digest that binds checks and exports to the exact revisions;
- line-ending preservation for edits made in a browser;
- policy flags for edits;
- git-compatible patches for added, changed and deleted files;
- applying recipe edits to a file that already changed in the workspace (exact position first,
  then the same lines found unambiguously nearby; anything else is a conflict, never a guess);
- the manifest of the derived snapshot that a workspace check scans.

Flags:
- Manual edits may legitimately touch configuration (a dependency upgrade), so their flags are
  information.
- Suppression markers and weakened tests are always flagged, and a finding that disappears
  because of a suppression is never counted as fixed.
- Recipe and AI edits keep the strict P05 policy.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from crp_analysis import policy as scope_policy
from crp_analysis.fixes import policy
from crp_analysis.fixes.patching import Edit, PatchError, apply_edits, relocate
from crp_analysis.manifest import ManifestEntry
from crp_core.domain.states import ChangeAction, FileDisposition

EXPORT_FORMAT = "crp-change-set-export/v1"
MANUAL_FLAGS = frozenset({"unsafe_path", "config_change", "suppression_added", "test_weakened"})
BLOCKING_FLAGS = frozenset({"unsafe_path"})


@dataclass(frozen=True, slots=True)
class FileChange:
    path: str
    action: ChangeAction
    base_sha256: str | None
    sha256: str | None


def content_digest(changes: Iterable[FileChange]) -> str:
    """Identity of a workspace's revisions (empty workspace included)."""
    rows = sorted([c.path, c.action.value, c.sha256 or ""] for c in changes)
    canonical = json.dumps({"version": 1, "files": rows}, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def keep_line_endings(base: str | None, text: str) -> str:
    """Browsers edit with LF; keep the file's CRLF line endings when the base used them."""
    if base is not None and "\r\n" in base and "\r\n" not in text:
        return text.replace("\n", "\r\n")
    return text


def edit_flags(path: str, before: str | None, after: str | None, *, strict: bool) -> list[str]:
    """Policy codes for an edit. Strict (recipes, AI) keeps every code; manual edits keep the
    ones that matter for honesty and safety."""
    codes = {v.code for v in policy.check(path, before or "", after or "", frozenset({path}))}
    if not strict:
        codes &= MANUAL_FLAGS
    return sorted(codes)


def _hunks(old: list[str], new: list[str], a: str, b: str, context: int) -> list[str]:
    out: list[str] = []
    for line in difflib.unified_diff(old, new, a, b, n=context):
        if line.endswith(("\n", "\r")):
            out.append(line)
        else:
            out.append(line + "\n\\ No newline at end of file\n")
    return out


def file_patch(
    path: str, before: str | None, after: str | None, *, mode: str = "100644", context: int = 3
) -> str:
    """Git-compatible diff of one file: changed (both texts), added (no before) or deleted."""
    old = before.splitlines(keepends=True) if before is not None else []
    new = after.splitlines(keepends=True) if after is not None else []
    header = f"diff --git a/{path} b/{path}\n"
    if before is None:
        return (
            header
            + f"new file mode {mode}\n"
            + "".join(_hunks(old, new, "/dev/null", f"b/{path}", context))
        )
    if after is None:
        return (
            header
            + f"deleted file mode {mode}\n"
            + "".join(_hunks(old, new, f"a/{path}", "/dev/null", context))
        )
    hunks = _hunks(old, new, f"a/{path}", f"b/{path}", context)
    return header + "".join(hunks) if hunks else ""


def apply_on_current(current: str, edits: list[Edit]) -> str:
    """Apply recipe edits (made against the upload) to the workspace's current text."""
    try:
        return apply_edits(current, edits)
    except PatchError:
        window = max(50, len(current.splitlines()))
        return apply_edits(current, [relocate(current, edit, window=window) for edit in edits])


def line_count(text: str) -> int:
    return text.count("\n") + (0 if text.endswith("\n") or not text else 1)


@dataclass(frozen=True, slots=True)
class RevisionInfo:
    """A workspace revision as the derived manifest needs it (no content required)."""

    change: FileChange
    size_bytes: int | None
    line_count: int | None


def derive_entries(base: list[ManifestEntry], revisions: list[RevisionInfo]) -> list[ManifestEntry]:
    """Manifest of base + workspace revisions, sorted by path."""
    by_path = {entry.path: entry for entry in base}
    for revision in revisions:
        change = revision.change
        if change.action is ChangeAction.DELETE:
            by_path.pop(change.path, None)
            continue
        existing = by_path.get(change.path)
        if existing is not None:
            by_path[change.path] = replace(
                existing,
                disposition=FileDisposition.ANALYZABLE,
                reason=None,
                size_bytes=revision.size_bytes,
                sha256=change.sha256,
                line_count=revision.line_count,
            )
            continue
        cls = scope_policy.classify(change.path)
        if cls.excluded_reason is not None:
            by_path[change.path] = ManifestEntry(
                change.path,
                FileDisposition.EXCLUDED,
                cls.excluded_reason,
                revision.size_bytes,
                None,
                cls.language,
                "excluded",
            )
        else:
            by_path[change.path] = ManifestEntry(
                change.path,
                FileDisposition.ANALYZABLE,
                None,
                revision.size_bytes,
                change.sha256,
                cls.language,
                cls.category,
                revision.line_count,
            )
    return sorted(by_path.values(), key=lambda entry: entry.path)


def language_of(path: str) -> str | None:
    return scope_policy.classify(path).language


def editable_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return (
        bool(path)
        and not pure.is_absolute()
        and ".." not in pure.parts
        and "\\" not in path
        and not path.endswith("/")
        and all(part and part not in {".", ".git"} for part in pure.parts)
    )
