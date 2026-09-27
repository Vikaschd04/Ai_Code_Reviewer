"""Capture an explicitly selected local folder into a deterministic ZIP plus a declared manifest.

The original folder is only read (never written, never executed). Symlinks and special files are
recorded but not followed or read; policy-excluded paths (secrets, VCS metadata, dependency and
build output) are skipped locally so they never leave this machine. Files are re-checked after
capture and the capture is retried a bounded number of times if anything changed meanwhile.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import stat
import tempfile
import unicodedata
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_VERSION = 1
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_CHUNK = 1024 * 1024


class CaptureError(RuntimeError):
    pass


class CaptureChangedError(CaptureError):
    """Files changed while being captured; the capture is inconsistent."""


@dataclass(frozen=True, slots=True)
class Policy:
    version: str
    excluded_directories: dict[str, str]
    secret_patterns: tuple[str, ...]
    secret_allowlist: frozenset[str]
    generated_patterns: tuple[str, ...]

    @classmethod
    def from_document(cls, document: dict[str, object]) -> Policy:
        dirs = document["excluded_directories"]
        if not isinstance(dirs, dict):
            raise CaptureError("policy document is malformed")
        return cls(
            version=str(document["version"]),
            excluded_directories={str(k): str(v) for k, v in dirs.items()},
            secret_patterns=tuple(str(p) for p in document["secret_patterns"]),  # type: ignore[attr-defined]
            secret_allowlist=frozenset(str(p) for p in document["secret_allowlist"]),  # type: ignore[attr-defined]
            generated_patterns=tuple(str(p) for p in document["generated_patterns"]),  # type: ignore[attr-defined]
        )

    def file_exclusion(self, name: str) -> str | None:
        lowered = name.lower()
        if lowered not in self.secret_allowlist and any(
            fnmatch.fnmatchcase(lowered, p) for p in self.secret_patterns
        ):
            return "secret_candidate"
        if any(fnmatch.fnmatchcase(lowered, p) for p in self.generated_patterns):
            return "generated_minified"
        return None


@dataclass(frozen=True, slots=True)
class CapturedFile:
    path: str
    sha256: str
    size_bytes: int
    signature: tuple[int, int, int]  # (size, mtime_ns, inode) used to detect concurrent changes


@dataclass(slots=True)
class Capture:
    root_label: str
    archive: Path
    files: list[CapturedFile] = field(default_factory=list)
    excluded: list[dict[str, object]] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)

    def digest(self) -> str:
        rows = sorted([f.path, f.size_bytes, f.sha256] for f in self.files)
        canonical = json.dumps(
            {"version": MANIFEST_VERSION, "files": rows}, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def client_manifest(self, runner_version: str, policy_version: str) -> dict[str, object]:
        return {
            "runner_version": runner_version,
            "policy_version": policy_version,
            "root_label": self.root_label,
            "manifest_digest": self.digest(),
            "files": [
                {"path": f.path, "sha256": f.sha256, "size_bytes": f.size_bytes} for f in self.files
            ],
            "excluded": self.excluded,
        }


def _signature(info: os.stat_result) -> tuple[int, int, int]:
    return (info.st_size, info.st_mtime_ns, info.st_ino)


def _current_signature(path: Path) -> tuple[int, int, int] | None:
    try:
        return _signature(path.lstat())
    except OSError:
        return None  # deleted or unreadable since it was captured


def _relative(root: Path, path: Path) -> str:
    return unicodedata.normalize("NFC", path.relative_to(root).as_posix())


def _scan_once(
    root: Path,
    policy: Policy,
    archive_path: Path,
    *,
    max_files: int,
    max_bytes: int,
    after_read: Callable[[str], None] | None,
) -> Capture:
    capture = Capture(root_label=root.name, archive=archive_path)
    unreadable_dirs: list[str] = []

    def on_error(error: OSError) -> None:
        if error.filename:
            unreadable_dirs.append(_relative(root, Path(error.filename)))

    total = 0
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for directory, dirnames, filenames in os.walk(root, followlinks=False, onerror=on_error):
            base = Path(directory)
            keep = []
            for name in sorted(dirnames):
                child = base / name
                rel = _relative(root, child)
                if child.is_symlink():
                    capture.excluded.append({"path": rel, "reason": "symlink", "kind": "directory"})
                elif reason := policy.excluded_directories.get(name):
                    capture.excluded.append({"path": rel, "reason": reason, "kind": "directory"})
                else:
                    keep.append(name)
            dirnames[:] = keep
            for name in sorted(filenames):
                path = base / name
                rel = _relative(root, path)
                try:
                    info = path.lstat()
                except OSError:
                    capture.excluded.append({"path": rel, "reason": "unreadable", "kind": "file"})
                    continue
                if stat.S_ISLNK(info.st_mode):
                    capture.excluded.append({"path": rel, "reason": "symlink", "kind": "file"})
                    continue
                if not stat.S_ISREG(info.st_mode):
                    capture.excluded.append({"path": rel, "reason": "special_file", "kind": "file"})
                    continue
                if reason := policy.file_exclusion(name):
                    capture.excluded.append(
                        {"path": rel, "reason": reason, "kind": "file", "size_bytes": info.st_size}
                    )
                    continue
                if len(capture.files) >= max_files:
                    raise CaptureError(
                        f"more than {max_files} files; the server would reject this capture"
                    )
                digest = hashlib.sha256()
                size = 0
                entry = zipfile.ZipInfo(rel, date_time=_ZIP_EPOCH)
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                try:
                    with path.open("rb") as source, zf.open(entry, "w") as sink:
                        while chunk := source.read(_CHUNK):
                            size += len(chunk)
                            total += len(chunk)
                            if total > max_bytes:
                                raise CaptureError(f"captured content exceeds {max_bytes} bytes")
                            digest.update(chunk)
                            sink.write(chunk)
                except PermissionError:
                    capture.excluded.append({"path": rel, "reason": "unreadable", "kind": "file"})
                    continue
                if after_read is not None:
                    after_read(rel)
                capture.files.append(CapturedFile(rel, digest.hexdigest(), size, _signature(info)))
    for rel in unreadable_dirs:
        capture.excluded.append({"path": rel, "reason": "unreadable", "kind": "directory"})
    changed = [f.path for f in capture.files if _current_signature(root / f.path) != f.signature]
    if changed:
        raise CaptureChangedError(
            f"{len(changed)} file(s) changed during capture, e.g. {changed[0]}"
        )
    return capture


def capture_folder(
    folder: Path,
    policy: Policy,
    *,
    max_files: int,
    max_bytes: int,
    attempts: int = 3,
    after_read: Callable[[str], None] | None = None,
) -> Capture:
    """Capture ``folder`` into a temporary ZIP outside it; the caller deletes ``archive``."""
    root = folder.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise CaptureError(f"{folder} is not a directory")
    temp_dir = Path(tempfile.gettempdir()).resolve()
    if temp_dir == root or root in temp_dir.parents:
        raise CaptureError(
            "the temporary directory is inside the captured folder; set TMPDIR elsewhere"
        )
    last: CaptureChangedError | None = None
    for _attempt in range(attempts):
        fd, name = tempfile.mkstemp(prefix="crp-capture-", suffix=".zip")
        os.close(fd)
        archive = Path(name)
        try:
            return _scan_once(
                root,
                policy,
                archive,
                max_files=max_files,
                max_bytes=max_bytes,
                after_read=after_read,
            )
        except CaptureChangedError as exc:
            archive.unlink(missing_ok=True)
            last = exc
        except BaseException:
            archive.unlink(missing_ok=True)
            raise
    raise CaptureChangedError(f"capture was inconsistent after {attempts} attempts: {last}")
