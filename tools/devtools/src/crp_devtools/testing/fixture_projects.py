"""Helpers that copy synthetic fixture projects into temporary directories for tests."""

from __future__ import annotations

import io
import os
import random
import shutil
import string
import zipfile
from pathlib import Path

from crp_devtools.paths import find_repo_root


def fixtures_root() -> Path:
    return find_repo_root(Path(__file__).resolve().parent) / "fixtures"


def prepare_fixture(name: str, destination: Path) -> Path:
    """Copy ``fixtures/projects/<name>`` to ``destination`` and add generated test-only content.

    Seeded fixtures get an AGENTS.md prompt-injection file; the security fixture gets fake,
    randomly shaped credentials. Both are generated here so that neither live instruction files
    nor secret-shaped strings are stored in this repository.
    """
    source = fixtures_root() / "projects" / name
    shutil.copytree(source, destination)
    injection = fixtures_root() / "untrusted-text" / f"{name}-agents-instructions.txt"
    if injection.is_file():
        (destination / "AGENTS.md").write_text(
            injection.read_text(encoding="utf-8"), encoding="utf-8"
        )
    if name == "security-mixed":
        _write_fake_secrets(destination)
    return destination


def _write_fake_secrets(root: Path) -> None:
    rng = random.Random(20260926)  # noqa: S311 - deterministic fake data, not cryptography
    alphabet = string.ascii_letters + string.digits
    token = "ghp_" + "".join(rng.choice(alphabet) for _ in range(36))
    (root / "web" / "src" / "config.js").write_text(
        "// Fake credential generated for tests (never valid).\n"
        f'export const githubToken = "{token}";\n',
        encoding="utf-8",
    )
    body = "\n".join("".join(rng.choice(alphabet + "+/") for _ in range(64)) for _ in range(10))
    key_dir = root / "src" / "main" / "resources"
    key_dir.mkdir(parents=True, exist_ok=True)
    (key_dir / "deploy-key.txt").write_text(
        f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----\n",
        encoding="utf-8",
    )


def zip_directory(root: Path) -> bytes:
    """ZIP every regular file under ``root`` (including excluded ones, as a user's zip would)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for directory, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(filenames):
                path = Path(directory) / name
                zf.write(path, path.relative_to(root).as_posix())
    return buffer.getvalue()
