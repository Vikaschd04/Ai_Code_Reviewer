"""Per-file engine result cache (project-scoped) with explicit invalidation inputs.

A cached result is reused only when every input that can change it is identical: workspace,
project, engine, engine version, rule-set hash, adapter configuration fingerprint, normalization
versions (fingerprint/correlation/catalog), materialization limit, path and blob SHA-256. Only
files the engine ANALYZED in a SUCCEEDED/PARTIAL run are stored. ``refresh`` scans bypass reads
and overwrite entries, so a full re-analysis is always possible.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from crp_analysis.engines.base import CacheIdentity
from crp_analysis.normalize import (
    NormalizedFinding,
    from_payload,
    normalization_identity,
    to_payload,
)
from crp_core.db.models import EngineCacheEntry

CACHE_KEY_VERSION = "crp-engine-cache-v1"


@dataclass(frozen=True, slots=True)
class CacheScope:
    workspace_id: UUID
    project_id: UUID
    engine: str
    engine_version: str
    identity: CacheIdentity
    max_file_bytes: int

    def key(self, path: str, blob_sha256: str) -> str:
        parts = (
            CACHE_KEY_VERSION,
            str(self.workspace_id),
            str(self.project_id),
            self.engine,
            self.engine_version,
            self.identity.ruleset_sha256,
            self.identity.config_fingerprint,
            normalization_identity(),
            str(self.max_file_bytes),
            path,
            blob_sha256,
        )
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


async def load_payloads(
    session: AsyncSession, project_id: UUID, keys: dict[str, str]
) -> dict[str, dict[str, object]]:
    """Return cached payloads by path for the given ``path -> cache key`` map."""
    if not keys:
        return {}
    by_key = {v: k for k, v in keys.items()}
    rows = (
        await session.execute(
            select(EngineCacheEntry.cache_key, EngineCacheEntry.payload).where(
                EngineCacheEntry.project_id == project_id,
                EngineCacheEntry.cache_key.in_(list(by_key)),
            )
        )
    ).all()
    return {by_key[key]: payload for key, payload in rows if isinstance(payload, dict)}


async def load(
    session: AsyncSession, scope: CacheScope, keys: dict[str, str]
) -> dict[str, list[NormalizedFinding]]:
    """Return cached normalized findings by path; malformed entries count as misses."""
    hits: dict[str, list[NormalizedFinding]] = {}
    for path, payload in (await load_payloads(session, scope.project_id, keys)).items():
        findings = payload.get("findings")
        if isinstance(findings, list):
            hits[path] = [from_payload(item) for item in findings]
    return hits


async def touch(session: AsyncSession, project_id: UUID, keys: Iterable[str]) -> None:
    keys = list(keys)
    if keys:
        await session.execute(
            update(EngineCacheEntry)
            .where(EngineCacheEntry.project_id == project_id, EngineCacheEntry.cache_key.in_(keys))
            .values(last_used_at=func.now(), hit_count=EngineCacheEntry.hit_count + 1)
        )


async def store_payloads(
    session: AsyncSession,
    scope: CacheScope,
    entries: dict[str, tuple[str, str, dict[str, object]]],
) -> None:
    """``entries``: path -> (cache key, blob sha, JSON payload)."""
    for key, blob, payload in entries.values():
        statement = insert(EngineCacheEntry).values(
            workspace_id=scope.workspace_id,
            project_id=scope.project_id,
            cache_key=key,
            engine=scope.engine,
            engine_version=scope.engine_version,
            ruleset_sha256=scope.identity.ruleset_sha256,
            file_sha256=blob,
            payload=payload,
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[EngineCacheEntry.project_id, EngineCacheEntry.cache_key],
                set_={
                    "payload": statement.excluded.payload,
                    "created_at": func.now(),
                    "last_used_at": func.now(),
                },
            )
        )


async def store(
    session: AsyncSession,
    scope: CacheScope,
    entries: dict[str, tuple[str, str, list[NormalizedFinding]]],
) -> None:
    """``entries``: path -> (cache key, blob sha, findings for that path)."""
    await store_payloads(
        session,
        scope,
        {
            path: (key, blob, {"path": path, "findings": [to_payload(f) for f in findings]})
            for path, (key, blob, findings) in entries.items()
        },
    )
