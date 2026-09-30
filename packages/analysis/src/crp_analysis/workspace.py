"""Materialize snapshot files into an isolated, read-only work directory for one engine run.

The original upload/folder is never touched; engines only ever see this copy. Paths come from the
validated manifest and are re-checked for containment before writing.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from crp_analysis.manifest import blob_key
from crp_core.artifacts import ArtifactKey, ArtifactStore


@dataclass(frozen=True, slots=True)
class WorkFile:
    path: str
    sha256: str


class WorkspaceError(RuntimeError):
    pass


def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for directory, dirnames, filenames in os.walk(path):
        for name in (*dirnames, *filenames):
            with contextlib.suppress(OSError):
                os.chmod(Path(directory) / name, stat.S_IRWXU)  # noqa: PTH101
    with contextlib.suppress(OSError):
        path.chmod(stat.S_IRWXU)
    shutil.rmtree(path, ignore_errors=True)


@contextlib.contextmanager
def materialized(
    store: ArtifactStore, work_dir: Path, files: list[WorkFile], *, max_file_bytes: int
) -> Iterator[Path]:
    """Yield ``work_dir/src`` containing the given files (read-only); remove it afterwards."""
    remove_tree(work_dir)
    root = work_dir / "src"
    root.mkdir(parents=True, mode=0o700)
    (work_dir / "home").mkdir(mode=0o700)
    resolved_root = root.resolve()
    try:
        for item in files:
            relative = PurePosixPath(item.path)
            if relative.is_absolute() or ".." in relative.parts:
                raise WorkspaceError("manifest path escapes the workspace")
            target = root.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if resolved_root not in target.parent.resolve().parents and (
                target.parent.resolve() != resolved_root
            ):
                raise WorkspaceError("manifest path escapes the workspace")
            data = store.read_bytes(ArtifactKey(blob_key(item.sha256)), max_bytes=max_file_bytes)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
        for directory, _dirnames, _filenames in os.walk(root, topdown=False):
            os.chmod(directory, 0o500)  # noqa: PTH101
        yield root
    finally:
        remove_tree(work_dir)


@contextlib.contextmanager
def written(work_dir: Path, files: dict[str, bytes]) -> Iterator[Path]:
    """Yield ``work_dir/src`` holding the given contents (read-only; e.g. a patched copy)."""
    remove_tree(work_dir)
    root = work_dir / "src"
    root.mkdir(parents=True, mode=0o700)
    (work_dir / "home").mkdir(mode=0o700)
    resolved_root = root.resolve()
    try:
        for path, data in files.items():
            relative = PurePosixPath(path)
            if relative.is_absolute() or ".." in relative.parts:
                raise WorkspaceError("path escapes the workspace")
            target = root.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if resolved_root not in target.parent.resolve().parents and (
                target.parent.resolve() != resolved_root
            ):
                raise WorkspaceError("path escapes the workspace")
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
        for directory, _dirnames, _filenames in os.walk(root, topdown=False):
            os.chmod(directory, 0o500)  # noqa: PTH101
        yield root
    finally:
        remove_tree(work_dir)
