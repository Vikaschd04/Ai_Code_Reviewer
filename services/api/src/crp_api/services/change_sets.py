"""Fix workspace storage (P08): file revisions in the artifact store, provenance events, digests.

The upload is never touched. A workspace records, per path:
- the action (modify, add, delete);
- the upload's content hash;
- the current revision's hash, size and line count;
- policy flags measured against the upload;
- where the changes came from.

Saving content equal to the upload drops the path from the workspace.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crp_analysis.fixes.changeset import (
    FileChange,
    content_digest,
    edit_flags,
    keep_line_endings,
    language_of,
    line_count,
)
from crp_analysis.manifest import blob_key
from crp_analysis.paths import PathRejectedError, canonical_path
from crp_api.errors import ApiError
from crp_api.services.snapshot_files import NotUtf8Error, read_blob_utf8
from crp_core.artifacts import ArtifactExistsError, ArtifactKey
from crp_core.db.models import ChangeSet, ChangeSetEvent, ChangeSetFile, FileEntry
from crp_core.domain.states import ChangeAction, ChangeSource, FileDisposition

NOT_EDITABLE = {
    FileDisposition.BINARY.value: "Binary files cannot be edited here.",
    FileDisposition.OVERSIZED.value: "This file is too large to edit here.",
    FileDisposition.EXCLUDED.value: "This file was not stored (for example a secrets file).",
}
NOT_UTF8 = "This file is not UTF-8 text, so it cannot be edited here without changing its bytes."


def canonical(container: Any, path: str) -> str:
    settings = container.settings
    try:
        clean = canonical_path(
            path,
            max_length=settings.intake_max_path_length,
            max_depth=settings.intake_max_path_depth,
        )
    except PathRejectedError as exc:
        raise ApiError(422, "invalid_path", str(exc)) from exc
    if clean.endswith("/") or clean.split("/")[0] == ".git":
        raise ApiError(422, "invalid_path", "This path cannot be edited")
    return clean


async def base_entry(session: AsyncSession, snapshot_id: uuid.UUID, path: str) -> FileEntry | None:
    return (
        await session.execute(
            select(FileEntry).where(FileEntry.snapshot_id == snapshot_id, FileEntry.path == path)
        )
    ).scalar_one_or_none()


async def files_of(session: AsyncSession, change_set_id: uuid.UUID) -> list[ChangeSetFile]:
    return list(
        (
            await session.execute(
                select(ChangeSetFile)
                .where(ChangeSetFile.change_set_id == change_set_id)
                .order_by(ChangeSetFile.path)
            )
        ).scalars()
    )


def changes_of(files: list[ChangeSetFile]) -> list[FileChange]:
    return [FileChange(f.path, ChangeAction(f.action), f.base_sha256, f.sha256) for f in files]


async def refresh_digest(session: AsyncSession, change_set: ChangeSet) -> str:
    await session.flush()
    change_set.content_sha256 = content_digest(changes_of(await files_of(session, change_set.id)))
    return change_set.content_sha256


async def store_text(container: Any, text: str) -> tuple[str, int]:
    data = text.encode("utf-8")
    if len(data) > container.settings.intake_max_text_file_bytes:
        raise ApiError(413, "file_too_large", "The file exceeds the per-file size limit")
    sha = hashlib.sha256(data).hexdigest()
    with contextlib.suppress(ArtifactExistsError):  # content-addressed: already stored
        await asyncio.to_thread(container.artifacts.put_bytes, ArtifactKey(blob_key(sha)), data)
    return sha, len(data)


async def current_text(
    session: AsyncSession, container: Any, change_set: ChangeSet, path: str
) -> tuple[str | None, str | None, ChangeSetFile | None, FileEntry | None]:
    """(upload text, current text, workspace row, upload entry); None where absent.

    Text is decoded strictly: a file that is not UTF-8 is refused (409 ``not_editable``) rather
    than shown with replacement characters that a save would write back.
    """
    entry = await base_entry(session, change_set.base_snapshot_id, path)
    row = (
        await session.execute(
            select(ChangeSetFile).where(
                ChangeSetFile.change_set_id == change_set.id, ChangeSetFile.path == path
            )
        )
    ).scalar_one_or_none()
    base_text = None
    try:
        if (
            entry is not None
            and entry.disposition == FileDisposition.ANALYZABLE.value
            and entry.blob_sha256
        ):
            base_text = await read_blob_utf8(container, entry.blob_sha256)
        if row is None:
            return base_text, base_text, None, entry
        if row.action == ChangeAction.DELETE.value or row.sha256 is None:
            return base_text, None, row, entry
        return base_text, await read_blob_utf8(container, row.sha256), row, entry
    except NotUtf8Error as exc:
        raise ApiError(409, "not_editable", NOT_UTF8) from exc


def _event(
    change_set: ChangeSet,
    source: ChangeSource,
    *,
    path: str | None,
    action: ChangeAction | None,
    summary: str,
    actor: uuid.UUID | None,
    finding_ids: list[uuid.UUID] | None = None,
    recipe_id: str | None = None,
    flags: list[str] | None = None,
    sha256: str | None = None,
) -> ChangeSetEvent:
    return ChangeSetEvent(
        change_set_id=change_set.id,
        source=source.value,
        path=path,
        action=action.value if action else None,
        finding_ids=[str(f) for f in finding_ids or []],
        recipe_id=recipe_id,
        summary=summary,
        flags=flags or [],
        sha256=sha256,
        content_sha256=change_set.content_sha256,
        actor_user_id=actor,
    )


async def _check_capacity(session: AsyncSession, container: Any, change_set: ChangeSet) -> None:
    count = await session.scalar(
        select(func.count()).where(ChangeSetFile.change_set_id == change_set.id)
    )
    if (count or 0) >= container.settings.change_set_max_files:
        raise ApiError(
            409,
            "workspace_full",
            f"A workspace can change at most {container.settings.change_set_max_files} files",
        )


async def save_text(
    session: AsyncSession,
    container: Any,
    change_set: ChangeSet,
    *,
    path: str,
    text: str,
    source: ChangeSource,
    actor: uuid.UUID | None,
    summary: str,
    finding_ids: list[uuid.UUID] | None = None,
    recipe_id: str | None = None,
    strict: bool = False,
) -> list[str]:
    """Record new content for ``path``; returns the policy flags (measured against the upload)."""
    base_text, _, row, entry = await current_text(session, container, change_set, path)
    if entry is not None and entry.disposition != FileDisposition.ANALYZABLE.value:
        raise ApiError(409, "not_editable", NOT_EDITABLE.get(entry.disposition, "Not editable"))
    text = keep_line_endings(base_text, text)
    if entry is not None and text == base_text:
        if row is not None:
            await session.delete(row)
            await refresh_digest(session, change_set)
            session.add(
                _event(
                    change_set,
                    ChangeSource.REVERT,
                    path=path,
                    action=None,
                    actor=actor,
                    summary="Back to the uploaded content",
                )
            )
        return []
    flags = edit_flags(path, base_text, text, strict=strict)
    if "unsafe_path" in flags:
        raise ApiError(422, "invalid_path", "This path cannot be edited")
    if row is None:
        await _check_capacity(session, container, change_set)
    sha, size = await store_text(container, text)
    action = ChangeAction.MODIFY if entry is not None else ChangeAction.ADD
    if row is None:
        row = ChangeSetFile(
            change_set_id=change_set.id,
            path=path,
            action=action.value,
            base_sha256=entry.blob_sha256 if entry is not None else None,
            flags=[],
            sources=[],
        )
        session.add(row)
    row.action = action.value
    row.sha256 = sha
    row.size_bytes = size
    row.line_count = line_count(text)
    row.language = (entry.language if entry is not None else None) or language_of(path)
    row.flags = flags
    row.sources = sorted({*row.sources, source.value})
    row.updated_by = actor
    await refresh_digest(session, change_set)
    session.add(
        _event(
            change_set,
            source,
            path=path,
            action=action,
            actor=actor,
            summary=summary,
            finding_ids=finding_ids,
            recipe_id=recipe_id,
            flags=flags,
            sha256=sha,
        )
    )
    return flags


async def delete_path(
    session: AsyncSession, container: Any, change_set: ChangeSet, path: str, actor: uuid.UUID | None
) -> None:
    _, _, row, entry = await current_text(session, container, change_set, path)
    if entry is None:
        if row is None:
            raise ApiError(404, "file_not_found", "The file is not in the upload or the workspace")
        await session.delete(row)  # an added file: just drop it
    else:
        if entry.disposition != FileDisposition.ANALYZABLE.value:
            raise ApiError(409, "not_editable", NOT_EDITABLE.get(entry.disposition, "Not editable"))
        if row is None:
            await _check_capacity(session, container, change_set)
            row = ChangeSetFile(
                change_set_id=change_set.id,
                path=path,
                action=ChangeAction.DELETE.value,
                base_sha256=entry.blob_sha256,
                flags=[],
                sources=[],
            )
            session.add(row)
        row.action = ChangeAction.DELETE.value
        row.base_sha256 = entry.blob_sha256
        row.sha256 = None
        row.size_bytes = None
        row.line_count = None
        row.flags = []
        row.sources = sorted({*row.sources, ChangeSource.MANUAL.value})
        row.updated_by = actor
    await refresh_digest(session, change_set)
    session.add(
        _event(
            change_set,
            ChangeSource.MANUAL,
            path=path,
            action=ChangeAction.DELETE,
            actor=actor,
            summary="File deleted in the workspace",
        )
    )


async def revert_path(
    session: AsyncSession, change_set: ChangeSet, path: str, actor: uuid.UUID | None
) -> None:
    removed = await session.execute(
        delete(ChangeSetFile).where(
            ChangeSetFile.change_set_id == change_set.id, ChangeSetFile.path == path
        )
    )
    if not removed.rowcount:  # type: ignore[attr-defined]
        raise ApiError(404, "file_not_changed", "The file has no changes in this workspace")
    await refresh_digest(session, change_set)
    session.add(
        _event(
            change_set,
            ChangeSource.REVERT,
            path=path,
            action=None,
            actor=actor,
            summary="Changes undone",
        )
    )


def export_event(change_set: ChangeSet, actor: uuid.UUID | None, fmt: str) -> ChangeSetEvent:
    return _event(
        change_set,
        ChangeSource.EXPORT,
        path=None,
        action=None,
        actor=actor,
        summary=f"Exported as {fmt}",
    )
