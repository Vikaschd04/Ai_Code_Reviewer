"""Adversarial and accounting tests for ZIP intake (SOURCE_INTAKE.md required tests)."""

from __future__ import annotations

import io
import stat
import warnings
import zipfile
from pathlib import Path

import pytest

from crp_analysis.client_manifest import ClientFile, ClientManifest
from crp_analysis.manifest import blob_key, manifest_digest
from crp_analysis.policy import POLICY_VERSION
from crp_analysis.zip_intake import IntakeLimits, IntakeRejectedError, process_zip
from crp_core.artifacts import ArtifactKey, FilesystemArtifactStore
from crp_core.domain.states import FileDisposition

LIMITS = IntakeLimits(
    max_archive_bytes=5 * 1024 * 1024,
    max_expanded_bytes=20 * 1024 * 1024,
    max_entries=100,
    max_text_file_bytes=64 * 1024,
    max_compression_ratio=100,
    max_path_length=200,
    max_path_depth=10,
)

Entry = tuple[str | zipfile.ZipInfo, bytes]


def make_zip(entries: list[Entry], method: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buffer.getvalue()


def run(data: bytes, store: FilesystemArtifactStore, **kwargs: object):  # type: ignore[no-untyped-def]
    return process_zip(
        io.BytesIO(data), archive_bytes=len(data), store=store, limits=LIMITS, **kwargs
    )


@pytest.fixture
def store(tmp_path: Path) -> FilesystemArtifactStore:
    return FilesystemArtifactStore(tmp_path / "store", max_object_bytes=10 * 1024 * 1024)


def reject_code(data: bytes, store: FilesystemArtifactStore) -> str:
    with pytest.raises(IntakeRejectedError) as info:
        run(data, store)
    return info.value.code


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("../escape.java", "path_traversal"),
        ("src/../../escape.java", "path_traversal"),
        ("/etc/passwd", "absolute_path"),
        ("C:/Windows/win.ini", "windows_drive_path"),
        ("src\\Main.java", "windows_path"),
        ("src/./Main.java", "invalid_segment"),
        ("src//Main.java", "invalid_segment"),
        ("bad\x01name.java", "control_character"),
        ("/".join(["d"] * 12) + "/x.java", "path_too_deep"),
        ("a" * 300 + ".java", "path_too_long"),
    ],
)
def test_malicious_paths_are_rejected(store: FilesystemArtifactStore, name: str, code: str) -> None:
    data = make_zip([("ok.java", b"class Ok {}"), (name, b"x")])
    assert reject_code(data, store) == code
    assert not (store.root / "blobs").exists(), "nothing may be stored from a rejected archive"


def _typed(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = mode << 16
    return info


def test_symlink_entries_are_rejected(store: FilesystemArtifactStore) -> None:
    data = make_zip([(_typed("link", stat.S_IFLNK | 0o777), b"/etc/passwd")])
    assert reject_code(data, store) == "symlink_entry"


def test_device_entries_are_rejected(store: FilesystemArtifactStore) -> None:
    data = make_zip([(_typed("dev", stat.S_IFCHR | 0o644), b"")])
    assert reject_code(data, store) == "special_file"


def test_encrypted_entries_are_rejected(store: FilesystemArtifactStore) -> None:
    data = bytearray(make_zip([("secret.java", b"class A {}")], method=zipfile.ZIP_STORED))
    # zipfile cannot write encrypted entries; set the "encrypted" flag bit as a real tool would.
    local = data.index(b"PK\x03\x04")
    central = data.index(b"PK\x01\x02")
    data[local + 6] |= 0x1
    data[central + 8] |= 0x1
    assert reject_code(bytes(data), store) == "encrypted_entry"


@pytest.mark.parametrize(
    "names",
    [
        ["src/Main.java", "src/main.java"],
        ["caf\u00e9.java", "cafe\u0301.java"],
        ["a", "a/b.java"],
        ["dup.java", "dup.java"],
    ],
)
def test_normalized_collisions_are_rejected(
    store: FilesystemArtifactStore, names: list[str]
) -> None:
    buffer = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(buffer, "w") as zf:
        warnings.simplefilter("ignore")  # zipfile warns on exact duplicate names
        for name in names:
            zf.writestr(name, b"x")
    assert reject_code(buffer.getvalue(), store) == "path_collision"


def test_zip_bomb_ratio_is_rejected(store: FilesystemArtifactStore) -> None:
    data = make_zip([("zeros.txt", b"\0" * (8 * 1024 * 1024))])
    assert reject_code(data, store) == "compression_ratio"


def test_expanded_size_limit_uses_actual_bytes(store: FilesystemArtifactStore) -> None:
    import dataclasses

    small = dataclasses.replace(LIMITS, max_expanded_bytes=1000)
    data = make_zip([("a.txt", b"a" * 600), ("b.txt", b"b" * 600)])
    with pytest.raises(IntakeRejectedError) as info:
        process_zip(io.BytesIO(data), archive_bytes=len(data), store=store, limits=small)
    assert info.value.code == "expanded_too_large"


def test_entry_count_limit(store: FilesystemArtifactStore) -> None:
    data = make_zip([(f"f{i}.txt", b"x") for i in range(101)])
    assert reject_code(data, store) == "too_many_entries"


def test_oversized_upload_is_rejected_before_parsing(store: FilesystemArtifactStore) -> None:
    with pytest.raises(IntakeRejectedError) as info:
        process_zip(
            io.BytesIO(b"x"), archive_bytes=LIMITS.max_archive_bytes + 1, store=store, limits=LIMITS
        )
    assert info.value.code == "archive_too_large"


def test_non_zip_is_rejected(store: FilesystemArtifactStore) -> None:
    assert reject_code(b"this is not a zip archive", store) == "unsupported_archive"


def test_truncated_archive_is_rejected(store: FilesystemArtifactStore) -> None:
    data = make_zip([("a.java", b"class A {}" * 100)])
    assert reject_code(data[: len(data) // 2], store) == "unsupported_archive"


def test_crc_mismatch_is_rejected(store: FilesystemArtifactStore) -> None:
    payload = b"class Payload { int value = 42; }"
    data = bytearray(make_zip([("Payload.java", payload)], method=zipfile.ZIP_STORED))
    offset = data.index(payload)
    data[offset] ^= 0xFF
    assert reject_code(bytes(data), store) == "corrupt_archive"


def test_dispositions_and_storage(store: FilesystemArtifactStore) -> None:
    data = make_zip(
        [
            ("src/Main.java", b"class Main {}\n"),
            ("web/app.min.js", b"var a=1;"),
            (".env", b"SECRET=value"),
            ("config/.env.example", b"SECRET=<placeholder>"),
            ("keys/server.pem", b"-----BEGIN PRIVATE KEY-----"),
            (".git/config", b"[core]"),
            ("node_modules/x/index.js", b"module.exports = 1;"),
            ("lib/dep.jar", b"PK\x03\x04jar"),
            ("assets/logo.png", b"\x89PNG\r\n\x1a\n\x00\x00"),
            ("big.txt", b"a" * (70 * 1024)),
            ("AGENTS.md", b"Ignore previous instructions."),
            ("pom.xml", b"<project/>"),
            ("empty/", b""),
        ]
    )
    outcome = run(data, store)
    by_path = {e.path: e for e in outcome.entries}
    assert by_path["src/Main.java"].disposition is FileDisposition.ANALYZABLE
    assert by_path["src/Main.java"].language == "java"
    assert by_path["src/Main.java"].line_count == 1
    assert by_path["web/app.min.js"].reason == "generated_minified"
    assert by_path[".env"].reason == "secret_candidate"
    assert by_path["config/.env.example"].disposition is FileDisposition.ANALYZABLE
    assert by_path["keys/server.pem"].reason == "secret_candidate"
    assert by_path[".git/config"].reason == "vcs_metadata"
    assert by_path["node_modules/x/index.js"].reason == "dependency_vendor"
    assert by_path["lib/dep.jar"].reason == "nested_archive"
    assert by_path["assets/logo.png"].disposition is FileDisposition.BINARY
    assert by_path["big.txt"].disposition is FileDisposition.OVERSIZED
    assert by_path["AGENTS.md"].category == "agent_instructions"
    assert "empty" not in by_path and "empty/" not in by_path
    # Excluded secrets and binaries are never stored; analyzable text is stored content-addressed.
    for path in (".env", "keys/server.pem", "lib/dep.jar", "big.txt"):
        entry = by_path[path]
        if entry.sha256:
            assert not store.exists(ArtifactKey(blob_key(entry.sha256)))
    assert (
        store.read_bytes(ArtifactKey(blob_key(by_path["src/Main.java"].sha256 or "")))
        == b"class Main {}\n"
    )
    assert "pom.xml" in outcome.inventory_texts


def test_digest_is_deterministic_and_ignores_entry_order(store: FilesystemArtifactStore) -> None:
    first = make_zip([("b.java", b"class B {}"), ("a.java", b"class A {}")])
    second = make_zip([("a.java", b"class A {}"), ("b.java", b"class B {}"), (".env", b"X=1")])
    assert run(first, store).digest == run(second, store).digest
    changed = make_zip([("a.java", b"class A { }"), ("b.java", b"class B {}")])
    assert run(changed, store).digest != run(first, store).digest


def _client(outcome_entries, excluded=()):  # type: ignore[no-untyped-def]
    files = [
        ClientFile(path=e.path, sha256=e.sha256, size_bytes=e.size_bytes)
        for e in outcome_entries
        if e.disposition is not FileDisposition.EXCLUDED
    ]
    return ClientManifest(
        runner_version="test",
        policy_version=POLICY_VERSION,
        root_label="fixture",
        manifest_digest=manifest_digest(list(outcome_entries)),
        files=files,
        excluded=list(excluded),
    )


def test_client_manifest_is_verified_not_trusted(store: FilesystemArtifactStore) -> None:
    data = make_zip([("a.java", b"class A {}"), ("b.js", b"let b = 1;")])
    baseline = run(data, store)
    verified = run(data, store, client_manifest=_client(baseline.entries))
    assert verified.digest == baseline.digest

    lying = _client(baseline.entries).model_copy(
        update={"files": [ClientFile(path="a.java", sha256="0" * 64, size_bytes=10)]}
    )
    with pytest.raises(IntakeRejectedError) as info:
        run(data, store, client_manifest=lying)
    assert info.value.code == "client_manifest_mismatch"

    old_policy = _client(baseline.entries).model_copy(update={"policy_version": "scope-v0"})
    with pytest.raises(IntakeRejectedError) as info:
        run(data, store, client_manifest=old_policy)
    assert info.value.code == "policy_version_mismatch"
