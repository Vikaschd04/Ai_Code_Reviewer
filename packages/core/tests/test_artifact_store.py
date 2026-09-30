"""Containment and integrity tests for the filesystem artifact store."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from crp_core.artifacts import (
    UNTRUSTED_MARKER,
    ArtifactContainmentError,
    ArtifactExistsError,
    ArtifactKey,
    ArtifactNotFoundError,
    ArtifactTooLargeError,
    FilesystemArtifactStore,
    InvalidArtifactKeyError,
)


@pytest.fixture
def store(tmp_path: Path) -> FilesystemArtifactStore:
    return FilesystemArtifactStore(tmp_path / "root", max_object_bytes=1024)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "../escape",
        "a/../../escape",
        "/etc/passwd",
        "a//b",
        "a/./b",
        "a\\b",
        ".hidden",
        "a/.git/config",
        "nul\x00byte",
        "-leading-dash",
        "a" * 129,
        "/".join(["d"] * 17),
        "x" * 513,
        "spaces are bad",
    ],
)
def test_unsafe_keys_are_rejected(key: str) -> None:
    with pytest.raises(InvalidArtifactKeyError):
        ArtifactKey(key)


def test_roundtrip_reports_sha256_and_size(store: FilesystemArtifactStore) -> None:
    key = ArtifactKey("snapshots/abc/manifest.json")
    ref = store.put_bytes(key, b"hello")
    assert ref.sha256 == hashlib.sha256(b"hello").hexdigest()
    assert ref.size_bytes == 5
    assert store.read_bytes(key) == b"hello"
    assert store.stat(key) == ref
    assert store.exists(key)


def test_put_does_not_overwrite_unless_requested(store: FilesystemArtifactStore) -> None:
    key = ArtifactKey("a/b.bin")
    store.put_bytes(key, b"one")
    with pytest.raises(ArtifactExistsError):
        store.put_bytes(key, b"two")
    assert store.read_bytes(key) == b"one"
    store.put_bytes(key, b"two", overwrite=True)
    assert store.read_bytes(key) == b"two"


def test_oversized_writes_fail_without_leaving_partial_files(
    store: FilesystemArtifactStore,
) -> None:
    key = ArtifactKey("big/object.bin")
    with pytest.raises(ArtifactTooLargeError):
        store.put_stream(key, [b"x" * 600, b"y" * 600])
    assert not store.exists(key)
    assert list((store.root / "big").iterdir()) == []


def test_bounded_reads(store: FilesystemArtifactStore) -> None:
    key = ArtifactKey("r/obj")
    store.put_bytes(key, b"0123456789")
    with pytest.raises(ArtifactTooLargeError):
        store.read_bytes(key, max_bytes=5)


def test_symlinked_directory_cannot_redirect_writes_outside_root(
    store: FilesystemArtifactStore, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "evil").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ArtifactContainmentError):
        store.put_bytes(ArtifactKey("evil/planted.txt"), b"data")
    assert list(outside.iterdir()) == []


def test_symlinked_directory_cannot_redirect_reads(
    store: FilesystemArtifactStore, tmp_path: Path
) -> None:
    outside = tmp_path / "secret-dir"
    outside.mkdir()
    (outside / "secret.txt").write_text("host secret")
    (store.root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ArtifactContainmentError):
        store.read_bytes(ArtifactKey("link/secret.txt"))


def test_symlinked_file_is_not_followed(store: FilesystemArtifactStore, tmp_path: Path) -> None:
    target = tmp_path / "host-file.txt"
    target.write_text("host content")
    (store.root / "dir").mkdir()
    (store.root / "dir" / "file.txt").symlink_to(target)
    with pytest.raises(ArtifactContainmentError):
        store.read_bytes(ArtifactKey("dir/file.txt"))
    with pytest.raises(ArtifactContainmentError):
        store.delete(ArtifactKey("dir/file.txt"))
    assert target.read_text() == "host content"


def test_symlinked_root_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ArtifactContainmentError):
        FilesystemArtifactStore(link, max_object_bytes=10)


def test_list_keys_returns_whole_segment_prefix_matches(
    store: FilesystemArtifactStore, tmp_path: Path
) -> None:
    for key in ("scans/s1/raw/pmd.json", "scans/s1/raw/eslint.json", "scans/s10/raw/pmd.json"):
        store.put_bytes(ArtifactKey(key), b"{}")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x")
    (store.root / "scans" / "s1" / "link").symlink_to(outside)
    assert [str(k) for k in store.list_keys("scans/s1")] == [
        "scans/s1/raw/eslint.json",
        "scans/s1/raw/pmd.json",
    ]
    assert store.list_keys("nothing/here") == []
    with pytest.raises(InvalidArtifactKeyError):
        store.list_keys("../etc")


def test_missing_objects(store: FilesystemArtifactStore) -> None:
    key = ArtifactKey("missing/obj")
    assert not store.exists(key)
    assert store.delete(key) is False
    with pytest.raises(ArtifactNotFoundError):
        store.read_bytes(key)


def test_files_are_owner_only_and_root_is_marked_untrusted(store: FilesystemArtifactStore) -> None:
    store.put_bytes(ArtifactKey("p/q"), b"data")
    mode = (store.root / "p" / "q").stat().st_mode & 0o777
    assert mode == 0o600
    assert (store.root / UNTRUSTED_MARKER).is_file()


def test_probe_leaves_no_residue(store: FilesystemArtifactStore) -> None:
    assert store.probe() == "filesystem"
    probes = store.root / "health-probes"
    assert not probes.exists() or list(probes.iterdir()) == []


@pytest.mark.integration
def test_postgres_store_honours_the_artifact_contract(database_url: str) -> None:
    from crp_core.artifacts.postgres import PostgresArtifactStore

    pg = PostgresArtifactStore(database_url, max_object_bytes=1024)
    try:
        key = ArtifactKey("blobs/ab/abc")
        ref = pg.put_stream(key, [b"hel", b"lo"])
        assert ref.sha256 == hashlib.sha256(b"hello").hexdigest() and ref.size_bytes == 5
        assert pg.read_bytes(key) == b"hello" and pg.stat(key) == ref and pg.exists(key)
        with pg.open_read(key) as handle:
            assert handle.read() == b"hello"
        with pytest.raises(ArtifactExistsError):
            pg.put_bytes(key, b"other")
        assert pg.read_bytes(key) == b"hello"
        pg.put_bytes(key, b"other", overwrite=True)
        assert pg.read_bytes(key) == b"other"
        with pytest.raises(ArtifactTooLargeError):
            pg.put_bytes(ArtifactKey("big/one"), b"x" * 1025)
        assert not pg.exists(ArtifactKey("big/one"))  # nothing partial is left behind
        with pytest.raises(ArtifactTooLargeError):
            pg.read_bytes(key, max_bytes=2)
        missing = ArtifactKey("nope/none")
        with pytest.raises(ArtifactNotFoundError):
            pg.read_bytes(missing)
        with pytest.raises(ArtifactNotFoundError):
            pg.stat(missing)
        for extra in ("scans/s1/raw/pmd.json", "scans/s1/raw/eslint.json", "scans/s10/x.json"):
            pg.put_bytes(ArtifactKey(extra), b"{}")
        pg.put_bytes(ArtifactKey("scans/s_1/raw.json"), b"{}")  # '_' is not a wildcard
        assert [str(k) for k in pg.list_keys("scans/s1")] == [
            "scans/s1/raw/eslint.json",
            "scans/s1/raw/pmd.json",
        ]
        assert pg.list_keys("nothing/here") == []
        assert pg.delete(key) and not pg.delete(key) and not pg.exists(key)
        assert pg.probe() == "postgres"
    finally:
        pg.close()
