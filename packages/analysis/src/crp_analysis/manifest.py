"""Canonical snapshot manifest and content-derived identity.

Identity covers every non-excluded regular file as (path, size, sha256) sorted by path plus the
manifest version. Excluded entries are listed for accounting but carry no content identity
because their bytes are intentionally not stored.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from crp_core.domain.states import FileDisposition

MANIFEST_VERSION = 1


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    path: str
    disposition: FileDisposition
    reason: str | None
    size_bytes: int | None
    sha256: str | None
    language: str | None
    category: str | None
    line_count: int | None = None


def blob_key(sha256: str) -> str:
    return f"blobs/{sha256[:2]}/{sha256}"


def identity_entries(entries: list[ManifestEntry]) -> list[tuple[str, int, str]]:
    rows = [
        (entry.path, entry.size_bytes or 0, entry.sha256 or "")
        for entry in entries
        if entry.disposition is not FileDisposition.EXCLUDED
    ]
    return sorted(rows)


def manifest_digest(entries: list[ManifestEntry]) -> str:
    canonical = json.dumps(
        {"version": MANIFEST_VERSION, "files": identity_entries(entries)},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def manifest_document(entries: list[ManifestEntry], *, policy_version: str) -> bytes:
    """Serialized manifest stored with the snapshot (all entries, sorted, deterministic)."""
    document = {
        "manifest_version": MANIFEST_VERSION,
        "policy_version": policy_version,
        "digest": manifest_digest(entries),
        "entries": [
            {**asdict(entry), "disposition": entry.disposition.value}
            for entry in sorted(entries, key=lambda item: item.path)
        ],
    }
    return json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8")
