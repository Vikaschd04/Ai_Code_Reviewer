"""Artifact-store contract and backends."""

from crp_core.artifacts.base import (
    ArtifactContainmentError,
    ArtifactError,
    ArtifactExistsError,
    ArtifactKey,
    ArtifactNotFoundError,
    ArtifactRef,
    ArtifactStore,
    ArtifactTooLargeError,
    InvalidArtifactKeyError,
)
from crp_core.artifacts.filesystem import UNTRUSTED_MARKER, FilesystemArtifactStore
from crp_core.config import ArtifactBackend, Settings


def create_artifact_store(settings: Settings) -> ArtifactStore:
    match settings.artifact_backend:
        case ArtifactBackend.FILESYSTEM:
            return FilesystemArtifactStore(
                settings.artifact_root, max_object_bytes=settings.artifact_max_object_bytes
            )


__all__ = [
    "UNTRUSTED_MARKER",
    "ArtifactContainmentError",
    "ArtifactError",
    "ArtifactExistsError",
    "ArtifactKey",
    "ArtifactNotFoundError",
    "ArtifactRef",
    "ArtifactStore",
    "ArtifactTooLargeError",
    "FilesystemArtifactStore",
    "InvalidArtifactKeyError",
    "create_artifact_store",
]
