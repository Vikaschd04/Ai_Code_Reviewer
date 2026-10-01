"""Streaming, bounded ZIP validation and extraction into the artifact store.

All entries are validated before any content is stored. Content is then read entry by entry with
cumulative and per-entry limits enforced on *actual* decompressed bytes (declared sizes can lie).
Accepted text files are stored content-addressed; binary/oversized files are hashed and recorded
but not stored; policy-excluded entries (secrets, VCS, vendor, build output) are recorded without
reading or storing their bytes. Nested archives are never unpacked.

Git provider archives (``GitArchiveOptions``) differ in three ways: their single top-level folder
is removed so paths match the repository, symbolic links are recorded as excluded instead of
rejecting the archive, and each file's Git blob id is computed so the capture can be checked
against the commit's tree (``crp_analysis.sources.capture``).
"""

from __future__ import annotations

import hashlib
import stat
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import IO

from crp_analysis import policy
from crp_analysis.client_manifest import ClientManifest
from crp_analysis.manifest import ManifestEntry, blob_key, manifest_digest
from crp_analysis.paths import (
    CollisionTracker,
    PathRejectedError,
    canonical_path,
    safe_display,
)
from crp_core.artifacts import ArtifactExistsError, ArtifactKey, ArtifactStore
from crp_core.domain.states import FileDisposition

_CHUNK = 256 * 1024
_SNIFF = 8192
_RATIO_MIN_ENTRY_BYTES = 1024 * 1024
_MAX_REPORTED_VIOLATIONS = 20
_INVENTORY_FILE_LIMIT = 1024 * 1024
_SUPPORTED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"
LFS_POINTER_MAX_BYTES = 1024


@dataclass(frozen=True, slots=True)
class IntakeLimits:
    max_archive_bytes: int
    max_expanded_bytes: int
    max_entries: int
    max_text_file_bytes: int
    max_compression_ratio: int
    max_path_length: int
    max_path_depth: int


class IntakeRejectedError(Exception):
    """The submission violates the intake contract. ``details`` never contains file contents."""

    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class GitArchiveOptions:
    """How a Git provider's commit archive is read (see the module docstring)."""

    strip_root: bool = True
    exclude_symlinks: bool = True
    blob_ids: bool = True


@dataclass(slots=True)
class IntakeOutcome:
    entries: list[ManifestEntry]
    digest: str
    archive_entries: int
    stored_bytes: int
    inventory_texts: dict[str, str] = field(default_factory=dict)
    # Git archives only: Git blob id (SHA-1 of "blob <size>\0" + bytes) of each file read, and
    # the permission bits of executable files.
    git_blob_ids: dict[str, str] = field(default_factory=dict)
    executable: set[str] = field(default_factory=set)
    lfs_pointers: set[str] = field(default_factory=set)

    @property
    def analyzable_count(self) -> int:
        return sum(e.disposition is FileDisposition.ANALYZABLE for e in self.entries)

    @property
    def excluded_count(self) -> int:
        return sum(e.disposition is FileDisposition.EXCLUDED for e in self.entries)


@dataclass(frozen=True, slots=True)
class _Planned:
    info: zipfile.ZipInfo
    path: str
    classification: policy.Classification


def _entry_mode(info: zipfile.ZipInfo) -> int | None:
    """Unix file-type bits, or None when the archive does not record a file type."""
    if info.create_system != 3:  # only Unix-created archives carry reliable mode bits
        return None
    mode = info.external_attr >> 16
    return mode if stat.S_IFMT(mode) else None


def _archive_root(infos: list[zipfile.ZipInfo]) -> str:
    """The single top-level folder every entry of a Git archive lives in (``owner-repo-sha/``)."""
    roots = {info.filename.split("/", 1)[0] for info in infos}
    if len(roots) != 1 or any("/" not in info.filename for info in infos):
        raise IntakeRejectedError(
            "unexpected_archive_layout",
            "The repository archive does not have the expected single top-level folder.",
        )
    return roots.pop() + "/"


def _plan(
    zf: zipfile.ZipFile,
    archive_bytes: int,
    limits: IntakeLimits,
    git: GitArchiveOptions | None = None,
) -> list[_Planned]:
    infos = zf.infolist()
    root = _archive_root(infos) if git is not None and git.strip_root and infos else ""
    if len(infos) > limits.max_entries:
        raise IntakeRejectedError(
            "too_many_entries",
            f"The archive has {len(infos)} entries; the limit is {limits.max_entries}.",
            {"entries": len(infos), "limit": limits.max_entries},
        )
    violations: list[dict[str, str]] = []

    def violate(code: str, name: str, message: str) -> None:
        if len(violations) < _MAX_REPORTED_VIOLATIONS:
            violations.append({"code": code, "entry": safe_display(name), "message": message})

    tracker = CollisionTracker()
    planned: list[_Planned] = []
    declared_total = 0
    for info in infos:
        name = info.filename[len(root) :]
        if not name:
            continue  # the archive's top-level folder itself
        is_dir = name.endswith("/")
        try:
            path = canonical_path(
                name.rstrip("/") if is_dir else name,
                max_length=limits.max_path_length,
                max_depth=limits.max_path_depth,
            )
        except PathRejectedError as exc:
            violate(exc.code, name, str(exc))
            continue
        if info.flag_bits & 0x1:
            violate("encrypted_entry", name, "encrypted archives are not supported")
            continue
        mode = _entry_mode(info)
        if mode is not None and stat.S_ISLNK(mode):
            if git is None or not git.exclude_symlinks:
                violate("symlink_entry", name, "symbolic links are not accepted")
            elif (conflict := tracker.add(path)) is not None:
                violate(
                    "path_collision",
                    name,
                    f"collides with '{safe_display(conflict)}' after case/Unicode normalization",
                )
            else:  # recorded, never followed or read
                excluded = policy.Classification("symlink", None, "excluded", nested_archive=False)
                planned.append(_Planned(info, path, excluded))
            continue
        if mode is not None and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            violate("special_file", name, "device, FIFO and socket entries are not accepted")
            continue
        if is_dir or (mode is not None and stat.S_ISDIR(mode)):
            continue
        if info.compress_type not in _SUPPORTED_COMPRESSION:
            violate(
                "unsupported_compression", name, "only stored and deflate entries are supported"
            )
            continue
        if (conflict := tracker.add(path)) is not None:
            violate(
                "path_collision",
                name,
                f"collides with '{safe_display(conflict)}' after case/Unicode normalization",
            )
            continue
        if (
            info.file_size >= _RATIO_MIN_ENTRY_BYTES
            and info.compress_size > 0
            and info.file_size / info.compress_size > limits.max_compression_ratio
        ):
            violate("compression_ratio", name, "entry exceeds the compression-ratio limit")
            continue
        declared_total += info.file_size
        planned.append(_Planned(info, path, policy.classify(path)))

    if violations:
        raise IntakeRejectedError(
            violations[0]["code"],
            f"The archive was rejected: {violations[0]['message']} ({violations[0]['entry']}).",
            {"violations": violations, "truncated": len(violations) >= _MAX_REPORTED_VIOLATIONS},
        )
    if declared_total > limits.max_expanded_bytes:
        raise IntakeRejectedError(
            "expanded_too_large",
            "The archive expands beyond the allowed snapshot size.",
            {"declared_bytes": declared_total, "limit": limits.max_expanded_bytes},
        )
    if (
        archive_bytes > 0
        and declared_total >= _RATIO_MIN_ENTRY_BYTES
        and declared_total / archive_bytes > limits.max_compression_ratio
    ):
        raise IntakeRejectedError(
            "compression_ratio",
            "The archive's overall compression ratio exceeds the limit.",
            {"ratio": round(declared_total / archive_bytes, 1)},
        )
    return sorted(planned, key=lambda item: item.path)


def _read_chunks(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> Iterator[bytes]:
    with zf.open(info) as handle:
        while chunk := handle.read(_CHUNK):
            yield chunk


def _line_count(data: bytes) -> int:
    return data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)


def process_zip(
    archive: IO[bytes],
    *,
    archive_bytes: int,
    store: ArtifactStore,
    limits: IntakeLimits,
    client_manifest: ClientManifest | None = None,
    git: GitArchiveOptions | None = None,
) -> IntakeOutcome:
    """Validate an archive and store accepted content. Raises IntakeRejectedError on violations."""
    if archive_bytes > limits.max_archive_bytes:
        raise IntakeRejectedError("archive_too_large", "The upload exceeds the size limit.")
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise IntakeRejectedError(
            "unsupported_archive", "The upload is not a readable ZIP archive."
        ) from exc
    with zf:
        planned = _plan(zf, archive_bytes, limits, git)
        entries: list[ManifestEntry] = []
        inventory_texts: dict[str, str] = {}
        blob_ids: dict[str, str] = {}
        executable: set[str] = set()
        lfs_pointers: set[str] = set()
        expanded = 0
        stored = 0
        for item in planned:
            cls = item.classification
            mode = _entry_mode(item.info)
            if git is not None and mode is not None and stat.S_ISREG(mode) and mode & 0o111:
                executable.add(item.path)
            if cls.excluded_reason is not None:
                entries.append(
                    ManifestEntry(
                        item.path,
                        FileDisposition.EXCLUDED,
                        cls.excluded_reason,
                        item.info.file_size,
                        None,
                        cls.language,
                        cls.category,
                    )
                )
                continue
            digest = hashlib.sha256()
            git_id = (
                hashlib.sha1(f"blob {item.info.file_size}\0".encode(), usedforsecurity=False)
                if git is not None and git.blob_ids
                else None
            )
            size = 0
            buffer: list[bytes] | None = []
            binary = cls.nested_archive
            try:
                for chunk in _read_chunks(zf, item.info):
                    if size == 0 and b"\x00" in chunk[:_SNIFF]:
                        binary = True
                    size += len(chunk)
                    expanded += len(chunk)
                    if expanded > limits.max_expanded_bytes:
                        raise IntakeRejectedError(
                            "expanded_too_large",
                            "The archive expands beyond the allowed snapshot size.",
                            {"limit": limits.max_expanded_bytes},
                        )
                    digest.update(chunk)
                    if git_id is not None:
                        git_id.update(chunk)
                    if buffer is not None:
                        if binary or size > limits.max_text_file_bytes:
                            buffer = None
                        else:
                            buffer.append(chunk)
            except (zipfile.BadZipFile, EOFError, ValueError) as exc:
                raise IntakeRejectedError(
                    "corrupt_archive",
                    "An archive entry is corrupt or its checksum does not match.",
                    {"entry": safe_display(item.info.filename)},
                ) from exc
            sha = digest.hexdigest()
            if git_id is not None:
                # A declared size that differs from the real one yields a non-matching id, which
                # the tree check treats like any other difference (the blob is fetched).
                blob_ids[item.path] = git_id.hexdigest()
            if binary:
                reason = "nested_archive" if cls.nested_archive else "binary_content"
                entries.append(
                    ManifestEntry(
                        item.path, FileDisposition.BINARY, reason, size, sha, None, "binary"
                    )
                )
                continue
            if buffer is None:
                entries.append(
                    ManifestEntry(
                        item.path,
                        FileDisposition.OVERSIZED,
                        "text_too_large",
                        size,
                        sha,
                        cls.language,
                        cls.category,
                    )
                )
                continue
            data = b"".join(buffer)
            if (
                git is not None
                and size <= LFS_POINTER_MAX_BYTES
                and data.startswith(LFS_POINTER_PREFIX)
            ):
                lfs_pointers.add(item.path)
            try:
                store.put_bytes(ArtifactKey(blob_key(sha)), data)
                stored += len(data)
            except ArtifactExistsError:
                pass  # content-addressed: identical bytes already stored
            if cls.category == "build" and size <= _INVENTORY_FILE_LIMIT:
                inventory_texts[item.path] = data.decode("utf-8", errors="replace")
            entries.append(
                ManifestEntry(
                    item.path,
                    FileDisposition.ANALYZABLE,
                    None,
                    size,
                    sha,
                    cls.language,
                    cls.category,
                    _line_count(data),
                )
            )

    if client_manifest is not None:
        entries = _reconcile_client_manifest(entries, client_manifest, limits)
    outcome = IntakeOutcome(
        entries=entries,
        digest=manifest_digest(entries),
        archive_entries=len(planned),
        stored_bytes=stored,
        inventory_texts=inventory_texts,
        git_blob_ids=blob_ids,
        executable=executable,
        lfs_pointers=lfs_pointers,
    )
    if client_manifest is not None and client_manifest.manifest_digest != outcome.digest:
        raise IntakeRejectedError(
            "client_manifest_mismatch",
            "The runner's declared manifest digest does not match the uploaded content.",
        )
    return outcome


def _reconcile_client_manifest(
    entries: list[ManifestEntry], client: ClientManifest, limits: IntakeLimits
) -> list[ManifestEntry]:
    """Verify runner-declared files against uploaded bytes and add its local exclusions."""
    if client.policy_version != policy.POLICY_VERSION:
        raise IntakeRejectedError(
            "policy_version_mismatch",
            f"The runner used scope policy {client.policy_version}; the server requires "
            f"{policy.POLICY_VERSION}. Update the runner.",
        )
    server = {
        e.path: (e.sha256, e.size_bytes)
        for e in entries
        if e.disposition is not FileDisposition.EXCLUDED
    }
    declared = {f.path: (f.sha256, f.size_bytes) for f in client.files}
    differences = sorted(set(server.items()) ^ set(declared.items()))
    if differences:
        raise IntakeRejectedError(
            "client_manifest_mismatch",
            "The runner's declared files do not match the uploaded archive.",
            {"examples": [safe_display(path) for path, _ in differences[:10]]},
        )
    present = {e.path for e in entries}
    for exclusion in client.excluded:
        try:
            path = canonical_path(
                exclusion.path, max_length=limits.max_path_length, max_depth=limits.max_path_depth
            )
        except PathRejectedError as exc:
            raise IntakeRejectedError(exc.code, f"Runner exclusion list: {exc}") from exc
        if path in present:
            raise IntakeRejectedError(
                "client_manifest_mismatch",
                "A path is both uploaded and declared excluded.",
                {"entry": safe_display(path)},
            )
        present.add(path)
        label = path + "/" if exclusion.kind == "directory" else path
        entries.append(
            ManifestEntry(
                label,
                FileDisposition.EXCLUDED,
                exclusion.reason,
                exclusion.size_bytes,
                None,
                None,
                "excluded",
            )
        )
    return entries
