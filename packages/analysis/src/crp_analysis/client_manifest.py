"""Manifest declared by the local runner. The server never trusts it: every declared file hash is
recomputed from the uploaded bytes and the declared digest must match the server's digest."""

from __future__ import annotations

from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from crp_analysis.policy import EXCLUDED_DIRECTORIES

ClientReason = Literal[
    "vcs_metadata",
    "dependency_vendor",
    "tooling_cache",
    "build_output",
    "ide_metadata",
    "tool_cache",
    "secret_candidate",
    "generated_minified",
    "symlink",
    "special_file",
    "unreadable",
]
if not set(EXCLUDED_DIRECTORIES.values()) <= set(get_args(ClientReason)):
    raise RuntimeError("ClientReason must cover every policy exclusion reason")

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
RelPath = Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClientFile(_Model):
    path: RelPath
    sha256: Sha256
    size_bytes: Annotated[int, Field(ge=0)]


class ClientExclusion(_Model):
    path: RelPath
    reason: ClientReason
    kind: Literal["file", "directory"]
    size_bytes: Annotated[int, Field(ge=0)] | None = None


class ClientManifest(_Model):
    runner_version: Annotated[str, StringConstraints(max_length=64)]
    policy_version: Annotated[str, StringConstraints(max_length=64)]
    root_label: Annotated[str, StringConstraints(max_length=200)]
    manifest_digest: Sha256
    files: Annotated[list[ClientFile], Field(max_length=1_000_000)]
    excluded: Annotated[list[ClientExclusion], Field(max_length=1_000_000)] = []
