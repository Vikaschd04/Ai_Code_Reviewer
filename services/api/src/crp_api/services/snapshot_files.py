"""Reading stored upload content for fixes and workspaces (text files only)."""

from __future__ import annotations

import asyncio
import posixpath
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crp_analysis.fixes import recipes
from crp_analysis.manifest import blob_key
from crp_api.errors import ApiError
from crp_core.artifacts import ArtifactKey
from crp_core.db.models import FileEntry
from crp_core.domain.states import FileDisposition


async def read_blob_bytes(container: Any, sha256: str) -> bytes:
    data: bytes = await asyncio.to_thread(
        container.artifacts.read_bytes,
        ArtifactKey(blob_key(sha256)),
        max_bytes=container.settings.intake_max_text_file_bytes,
    )
    return data


async def read_blob_text(container: Any, sha256: str) -> str:
    """Text for display and analysis; undecodable bytes become U+FFFD (never written back)."""
    return (await read_blob_bytes(container, sha256)).decode("utf-8", errors="replace")


class NotUtf8Error(ValueError):
    """Stored content that is not UTF-8 text (it cannot be edited without changing bytes)."""


async def read_blob_utf8(container: Any, sha256: str) -> str:
    """Exact text for editing; raises NotUtf8Error instead of replacing bytes."""
    try:
        return (await read_blob_bytes(container, sha256)).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NotUtf8Error(sha256) from exc


async def entry_text(container: Any, entry: FileEntry) -> str:
    if entry.blob_sha256 is None or entry.disposition != FileDisposition.ANALYZABLE.value:
        raise ApiError(409, "content_not_stored", "The file's content is not stored")
    return await read_blob_text(container, entry.blob_sha256)


async def sfdx_reader(
    session: AsyncSession, container: Any, snapshot_id: uuid.UUID, near: str
) -> recipes.ReadFile:
    """Read callback for recipes: the ``sfdx-project.json`` closest above ``near``."""
    candidates = (
        await session.scalars(
            select(FileEntry).where(
                FileEntry.snapshot_id == snapshot_id,
                FileEntry.path.like("%sfdx-project.json"),
                FileEntry.disposition == FileDisposition.ANALYZABLE.value,
            )
        )
    ).all()
    best: FileEntry | None = None
    for entry in candidates:
        directory = posixpath.dirname(entry.path)
        if (not directory or near.startswith(directory + "/")) and (
            best is None or len(directory) > len(posixpath.dirname(best.path))
        ):
            best = entry
    project_text = await entry_text(container, best) if best is not None else None

    def read(name: str) -> str | None:
        return project_text if name == "sfdx-project.json" else None

    return read
