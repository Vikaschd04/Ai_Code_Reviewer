"""Narrow artifact-store contract.

Artifacts (snapshot archives, raw engine output, patches) are addressed by validated relative
keys, never by caller-supplied filesystem paths. Implementations must guarantee that no key can
read or write outside the store, and must report content hashes so workflows can pass references
and digests instead of source bodies.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import IO, Protocol, runtime_checkable

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_KEY_LENGTH = 512
MAX_KEY_DEPTH = 16


class ArtifactError(Exception):
    """Base class for artifact-store failures. Messages never include absolute host paths."""


class InvalidArtifactKeyError(ArtifactError):
    pass


class ArtifactNotFoundError(ArtifactError):
    pass


class ArtifactExistsError(ArtifactError):
    pass


class ArtifactTooLargeError(ArtifactError):
    pass


class ArtifactContainmentError(ArtifactError):
    """A resolved location escaped the store root (for example through a planted symlink)."""


@dataclass(frozen=True, slots=True)
class ArtifactKey:
    """A validated relative artifact key such as ``snapshots/<id>/manifest.json``."""

    value: str

    def __post_init__(self) -> None:
        validate_key(self.value)

    @property
    def segments(self) -> tuple[str, ...]:
        return tuple(self.value.split("/"))

    def __str__(self) -> str:
        return self.value


def validate_key(value: str) -> None:
    if not value or len(value) > MAX_KEY_LENGTH:
        raise InvalidArtifactKeyError("artifact key must be 1-512 characters")
    if "\\" in value or "\x00" in value:
        raise InvalidArtifactKeyError("artifact key contains a forbidden character")
    segments = value.split("/")
    if len(segments) > MAX_KEY_DEPTH:
        raise InvalidArtifactKeyError("artifact key is nested too deeply")
    for segment in segments:
        if segment in {"", ".", ".."} or not _SEGMENT.fullmatch(segment):
            raise InvalidArtifactKeyError(
                "artifact key segments must start with a letter or digit and use only "
                "letters, digits, '.', '_' or '-'"
            )


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    key: ArtifactKey
    sha256: str
    size_bytes: int


@runtime_checkable
class ArtifactStore(Protocol):
    """Storage contract implemented by the filesystem backend (and later an S3-compatible one)."""

    def put_bytes(
        self, key: ArtifactKey, data: bytes, *, overwrite: bool = False
    ) -> ArtifactRef: ...

    def put_stream(
        self, key: ArtifactKey, chunks: Iterable[bytes], *, overwrite: bool = False
    ) -> ArtifactRef: ...

    def read_bytes(self, key: ArtifactKey, *, max_bytes: int | None = None) -> bytes: ...

    def open_read(self, key: ArtifactKey) -> AbstractContextManager[IO[bytes]]:
        """Open an artifact for streaming/seekable reads (e.g. archive validation)."""
        ...

    def stat(self, key: ArtifactKey) -> ArtifactRef: ...

    def exists(self, key: ArtifactKey) -> bool: ...

    def delete(self, key: ArtifactKey) -> bool: ...

    def list_keys(self, prefix: str) -> list[ArtifactKey]:
        """Keys under a key prefix made of whole segments (e.g. ``"scans/<id>"``), sorted."""
        ...

    def probe(self) -> str:
        """Verify the backend is usable; return a short non-sensitive description."""
        ...
