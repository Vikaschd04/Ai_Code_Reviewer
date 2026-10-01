"""Commit capture: a provider's archive of a commit, checked against the commit's tree (P06).

GitHub builds archives with ``git archive``, which applies repository attributes:
- ``export-ignore`` leaves files out of the archive;
- ``export-subst``, ``ident`` and line-ending settings change file bytes;
- Git LFS files may appear as pointers or as their stored objects.

A pull request could add ``export-ignore`` to hide a file from review, so every capture is
compared with the tree listing of the exact commit:

- Bytes whose Git blob id matches the tree are kept as they are.
- Line-ending conversion (CRLF) is recognised and undone without a request. The stored bytes are
  then the committed ones.
- Files missing from the archive, or whose bytes differ for another reason, are fetched one by one
  as the committed blob, up to a limit. Beyond it they are recorded as excluded with the reason, so
  coverage says so.
- Symbolic links and submodules are recorded as excluded; they are never followed or fetched.
- Git LFS pointer files are recorded as excluded (the stored object is not part of the commit).

Afterwards every analyzable file is byte-identical to the commit.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from crp_analysis import policy
from crp_analysis.manifest import ManifestEntry, blob_key, manifest_digest
from crp_analysis.paths import PathRejectedError, canonical_path
from crp_analysis.sources.github import TreeEntry, git_blob_id
from crp_analysis.zip_intake import (
    LFS_POINTER_MAX_BYTES,
    LFS_POINTER_PREFIX,
    IntakeLimits,
    IntakeOutcome,
    IntakeRejectedError,
)
from crp_core.artifacts import ArtifactExistsError, ArtifactKey, ArtifactStore
from crp_core.domain.states import FileDisposition

_SNIFF = 8192
_LISTED = 20
_MAX_EXECUTABLES = 20_000
_INVENTORY_FILE_LIMIT = 1024 * 1024

FetchBlob = Callable[[str], Awaitable[bytes]]


@dataclass(slots=True)
class _Report:
    tree_files: int = 0
    verified: int = 0
    eol_normalized: int = 0
    fetched: list[str] = field(default_factory=list)
    not_in_archive: list[str] = field(default_factory=list)
    differs: list[str] = field(default_factory=list)
    submodules: list[str] = field(default_factory=list)
    lfs: list[str] = field(default_factory=list)
    symlinks: int = 0
    unsupported_paths: int = 0

    def as_dict(self, executable: list[str], truncated: bool) -> dict[str, object]:
        def listed(paths: list[str]) -> dict[str, object]:
            return {"count": len(paths), "paths": sorted(paths)[:_LISTED]}

        return {
            "tree_files": self.tree_files,
            "verified": self.verified,
            "eol_normalized": self.eol_normalized,
            "fetched": listed(self.fetched),
            "not_in_archive": listed(self.not_in_archive),
            "differs_from_commit": listed(self.differs),
            "submodules": listed(self.submodules),
            "git_lfs": listed(self.lfs),
            "symlinks": self.symlinks,
            "unsupported_paths": self.unsupported_paths,
            "tree_truncated": truncated,
            "executable": sorted(executable)[:_MAX_EXECUTABLES],
            "executable_truncated": len(executable) > _MAX_EXECUTABLES,
        }


def _line_count(data: bytes) -> int:
    return data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)


def _excluded(path: str, reason: str, size: int | None) -> ManifestEntry:
    return ManifestEntry(path, FileDisposition.EXCLUDED, reason, size, None, None, "excluded")


def _is_lfs_pointer(data: bytes) -> bool:
    return len(data) <= LFS_POINTER_MAX_BYTES and data.startswith(LFS_POINTER_PREFIX)


class _Store:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    async def put(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        with contextlib.suppress(ArtifactExistsError):  # content-addressed: already stored
            await asyncio.to_thread(self._store.put_bytes, ArtifactKey(blob_key(sha)), data)
        return sha

    async def get(self, sha: str, max_bytes: int) -> bytes:
        return await asyncio.to_thread(
            self._store.read_bytes, ArtifactKey(blob_key(sha)), max_bytes=max_bytes
        )


def _entry_from_bytes(path: str, data: bytes, limits: IntakeLimits, sha: str) -> ManifestEntry:
    cls = policy.classify(path)
    if b"\x00" in data[:_SNIFF] or cls.nested_archive:
        reason = "nested_archive" if cls.nested_archive else "binary_content"
        return ManifestEntry(path, FileDisposition.BINARY, reason, len(data), sha, None, "binary")
    if len(data) > limits.max_text_file_bytes:
        return ManifestEntry(
            path,
            FileDisposition.OVERSIZED,
            "text_too_large",
            len(data),
            sha,
            cls.language,
            cls.category,
        )
    return ManifestEntry(
        path,
        FileDisposition.ANALYZABLE,
        None,
        len(data),
        sha,
        cls.language,
        cls.category,
        _line_count(data),
    )


async def reconcile_with_tree(
    outcome: IntakeOutcome,
    tree: list[TreeEntry],
    *,
    truncated: bool,
    fetch: FetchBlob,
    store: ArtifactStore,
    limits: IntakeLimits,
    max_fetches: int,
) -> tuple[IntakeOutcome, dict[str, object]]:
    """Make the archive's entries match the commit's tree (see the module docstring)."""
    entries = {entry.path: entry for entry in outcome.entries}
    report = _Report()
    blobs = _Store(store)
    inventory = dict(outcome.inventory_texts)
    executable: list[str] = []
    files: dict[str, TreeEntry] = {}
    for item in tree:
        try:
            path = canonical_path(
                item.path, max_length=limits.max_path_length, max_depth=limits.max_path_depth
            )
        except PathRejectedError:
            report.unsupported_paths += 1
            continue
        if item.kind == "commit":
            report.submodules.append(path)
            entries[path + "/"] = _excluded(path + "/", "submodule", None)
        elif item.kind == "blob":
            files[path] = item
    if not truncated:
        extra = sorted(
            path
            for path, entry in entries.items()
            if path not in files and not path.endswith("/") and entry.reason != "symlink"
        )
        if extra:
            raise IntakeRejectedError(
                "archive_mismatch",
                "GitHub's archive contains files that are not part of the commit.",
                {"examples": extra[:10]},
            )
    report.tree_files = len(files)
    fetches = 0
    for path, item in sorted(files.items()):
        entry = entries.get(path)
        if item.mode == "120000":
            report.symlinks += 1
            entries[path] = _excluded(path, "symlink", item.size)
            continue
        if item.mode == "100755":
            executable.append(path)
        cls = policy.classify(path)
        if cls.excluded_reason is not None:
            if entry is None:
                entries[path] = _excluded(path, cls.excluded_reason, item.size)
            continue
        if path in outcome.lfs_pointers:
            report.lfs.append(path)
            entries[path] = _excluded(path, "git_lfs", entry.size_bytes if entry else item.size)
            continue
        if entry is not None and outcome.git_blob_ids.get(path) == item.sha:
            report.verified += 1
            continue
        if (
            entry is not None
            and entry.disposition is FileDisposition.ANALYZABLE
            and entry.sha256 is not None
        ):
            stored = await blobs.get(entry.sha256, limits.max_text_file_bytes)
            if b"\r\n" in stored:
                unix = stored.replace(b"\r\n", b"\n")
                if git_blob_id(unix) == item.sha:
                    sha = await blobs.put(unix)
                    entries[path] = _entry_from_bytes(path, unix, limits, sha)
                    report.eol_normalized += 1
                    continue
        missing = entry is None
        too_big = (item.size or 0) > limits.max_text_file_bytes
        if fetches >= max_fetches or too_big:
            (report.not_in_archive if missing else report.differs).append(path)
            reason = "not_in_archive" if missing else "differs_from_commit"
            entries[path] = _excluded(path, reason, item.size)
            continue
        data = await fetch(item.sha)
        fetches += 1
        if _is_lfs_pointer(data):
            report.lfs.append(path)
            entries[path] = _excluded(path, "git_lfs", len(data))
            continue
        replacement = _entry_from_bytes(path, data, limits, hashlib.sha256(data).hexdigest())
        if replacement.disposition is FileDisposition.ANALYZABLE:
            await blobs.put(data)
            if replacement.category == "build" and len(data) <= _INVENTORY_FILE_LIMIT:
                inventory[path] = data.decode("utf-8", errors="replace")
        entries[path] = replacement
        report.fetched.append(path)

    ordered = sorted(entries.values(), key=lambda entry: entry.path)
    reconciled = IntakeOutcome(
        entries=ordered,
        digest=manifest_digest(ordered),
        archive_entries=outcome.archive_entries,
        stored_bytes=outcome.stored_bytes,
        inventory_texts=inventory,
        git_blob_ids=outcome.git_blob_ids,
        executable=set(executable),
        lfs_pointers=outcome.lfs_pointers,
    )
    return reconciled, report.as_dict(executable, truncated)
