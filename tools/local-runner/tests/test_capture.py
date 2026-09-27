"""Local-folder capture: exclusions, read-only guarantee, change detection, determinism."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
from typing import Any

import pytest

from crp_analysis.policy import policy_document
from crp_analysis.zip_intake import IntakeLimits, process_zip
from crp_core.artifacts import FilesystemArtifactStore
from crp_devtools.testing.fixture_projects import prepare_fixture
from crp_runner.capture import CaptureChangedError, Policy, capture_folder
from crp_runner.cli import capture_and_upload

POLICY = Policy.from_document(policy_document())
LIMITS = IntakeLimits(
    max_archive_bytes=50_000_000,
    max_expanded_bytes=50_000_000,
    max_entries=10_000,
    max_text_file_bytes=1_000_000,
    max_compression_ratio=100,
    max_path_length=1024,
    max_path_depth=64,
)


def tree_state(root: Path) -> dict[str, tuple[str, int, int]]:
    state = {}
    for directory, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            path = Path(directory) / name
            info = path.lstat()
            content = b"" if path.is_symlink() or not path.is_file() else path.read_bytes()
            state[str(path.relative_to(root))] = (
                hashlib.sha256(content).hexdigest(),
                info.st_mtime_ns,
                info.st_mode,
            )
    return state


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = prepare_fixture("seeded-mixed", tmp_path / "project")
    outside = tmp_path / "outside.txt"
    outside.write_text("host secret outside the selected folder")
    (root / "linked.txt").symlink_to(outside)
    (root / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    os.mkfifo(root / "pipe")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")
    return root


def test_capture_skips_excluded_paths_locally(folder: Path) -> None:
    capture = capture_folder(folder, POLICY, max_files=1000, max_bytes=10_000_000)
    try:
        paths = {f.path for f in capture.files}
        assert "src/main/java/com/example/billing/InvoiceService.java" in paths
        assert "AGENTS.md" in paths
        for skipped in (
            ".env",
            "linked.txt",
            "pipe",
            "node_modules/leftpad/index.js",
            ".git/config",
        ):
            assert skipped not in paths
        reasons = {(e["path"], e["reason"]) for e in capture.excluded}
        assert (".env", "secret_candidate") in reasons
        assert ("linked.txt", "symlink") in reasons
        assert ("linked-dir", "symlink") in reasons
        assert ("pipe", "special_file") in reasons
        assert ("node_modules", "dependency_vendor") in reasons
        assert (".git", "vcs_metadata") in reasons
    finally:
        capture.archive.unlink()


def test_capture_digest_matches_server_manifest(folder: Path, tmp_path: Path) -> None:
    capture = capture_folder(folder, POLICY, max_files=1000, max_bytes=10_000_000)
    try:
        data = capture.archive.read_bytes()
        store = FilesystemArtifactStore(tmp_path / "store", max_object_bytes=10_000_000)
        outcome = process_zip(io.BytesIO(data), archive_bytes=len(data), store=store, limits=LIMITS)
        assert outcome.digest == capture.digest()
    finally:
        capture.archive.unlink()


def test_original_folder_is_unchanged(folder: Path) -> None:
    before = tree_state(folder)
    capture = capture_folder(folder, POLICY, max_files=1000, max_bytes=10_000_000)
    capture.archive.unlink()
    assert tree_state(folder) == before
    assert not capture.archive.exists()


def test_capture_is_deterministic(folder: Path) -> None:
    first = capture_folder(folder, POLICY, max_files=1000, max_bytes=10_000_000)
    second = capture_folder(folder, POLICY, max_files=1000, max_bytes=10_000_000)
    try:
        assert first.archive.read_bytes() == second.archive.read_bytes()
    finally:
        first.archive.unlink()
        second.archive.unlink()


def test_concurrent_change_is_retried(folder: Path) -> None:
    target = folder / "README.md"
    calls = {"n": 0}

    def mutate_once(path: str) -> None:
        if path == "README.md" and calls["n"] == 0:
            calls["n"] += 1
            os.utime(target, ns=(1, 1))

    capture = capture_folder(
        folder, POLICY, max_files=1000, max_bytes=10_000_000, after_read=mutate_once
    )
    capture.archive.unlink()
    assert calls["n"] == 1


def test_persistent_changes_fail_the_capture(folder: Path) -> None:
    counter = {"n": 0}

    def always_mutate(path: str) -> None:
        if path == "README.md":
            counter["n"] += 1
            os.utime(folder / "README.md", ns=(counter["n"], counter["n"]))

    with pytest.raises(CaptureChangedError):
        capture_folder(
            folder, POLICY, max_files=1000, max_bytes=10_000_000, after_read=always_mutate
        )


class RecordingPlatform:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, path: str) -> Any:
        self.calls.append(f"GET {path}")
        return {
            **policy_document(),
            "limits": {"max_entries": 1000, "max_expanded_bytes": 10_000_000},
        }

    def post(self, path: str, body: Any = None) -> Any:
        self.calls.append(f"POST {path}")
        raise AssertionError("nothing may be sent")


def test_dry_run_and_refusal_send_nothing(folder: Path) -> None:
    for dry_run, answer in ((True, "yes"), (False, "no")):
        platform = RecordingPlatform()
        output: list[str] = []
        result = capture_and_upload(
            folder,
            "project",
            platform,  # type: ignore[arg-type]
            api_url="http://127.0.0.1:8710",
            dry_run=dry_run,
            assume_yes=False,
            start_scan=False,
            out=output.append,
            confirm=lambda _prompt, a=answer: a,
        )
        assert result is None
        assert platform.calls == ["GET /v1/intake-policy"]
        assert any("leave this machine" in line for line in output)


def test_unreadable_files_are_disclosed_not_silently_dropped(folder: Path) -> None:
    locked = folder / "src" / "main" / "java" / "com" / "example" / "billing" / "Locked.java"
    locked.write_text("class Locked {}")
    locked.chmod(0o000)
    try:
        capture = capture_folder(folder, POLICY, max_files=1000, max_bytes=10_000_000)
        capture.archive.unlink()
    finally:
        locked.chmod(0o600)
    rel = "src/main/java/com/example/billing/Locked.java"
    assert rel not in {f.path for f in capture.files}
    assert {"path": rel, "reason": "unreadable", "kind": "file"} in capture.excluded
