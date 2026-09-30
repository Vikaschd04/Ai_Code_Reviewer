"""Generation and loading of local development secrets kept outside source control."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

MIN_TOKEN_LENGTH = 32


class SecretFileError(RuntimeError):
    """A secret file is missing, unsafe or malformed. Messages never include the secret value."""


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def write_secret_file(path: Path, value: str, *, overwrite: bool = False) -> bool:
    """Create ``path`` with owner-only permissions. Returns False if it already existed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
    flags |= os.O_TRUNC if overwrite else os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value + "\n")
    path.chmod(0o600)
    return True


def read_secret_file(path: Path, *, min_length: int = MIN_TOKEN_LENGTH) -> str:
    """Read a secret file, refusing symlinks, non-regular files and group/world-readable modes."""
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise SecretFileError(
            f"secret file not found: {path.name} (run 'make bootstrap' to generate it)"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SecretFileError(f"secret file {path.name} must be a regular file, not a link")
    if info.st_mode & 0o077:
        raise SecretFileError(
            f"secret file {path.name} is accessible by other users; run: chmod 600 <file>"
        )
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < min_length:
        raise SecretFileError(f"secret file {path.name} is too short to be a generated secret")
    return value
