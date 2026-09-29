"""PostgreSQL-backed artifact store for lite deployments that have no persistent disk.

Same contract as the filesystem store: validated keys, no silent overwrite, size-bounded writes
and reads, content hashes. Objects are held in memory while written or read, so the configured
``max_object_bytes`` must stay small (lite profile defaults keep uploads to tens of megabytes).
"""

from __future__ import annotations

import hashlib
import io
import os
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import IO

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from crp_core.artifacts.base import (
    ArtifactError,
    ArtifactExistsError,
    ArtifactKey,
    ArtifactNotFoundError,
    ArtifactRef,
    ArtifactTooLargeError,
)
from crp_core.db.models import ArtifactObject


class PostgresArtifactStore:
    def __init__(self, database_url: str, *, max_object_bytes: int, pool_size: int = 2) -> None:
        self._engine: Engine = create_engine(
            database_url, pool_size=pool_size, max_overflow=2, pool_pre_ping=True
        )
        self._max_object_bytes = max_object_bytes

    def close(self) -> None:
        self._engine.dispose()

    def put_bytes(self, key: ArtifactKey, data: bytes, *, overwrite: bool = False) -> ArtifactRef:
        return self.put_stream(key, [data], overwrite=overwrite)

    def put_stream(
        self, key: ArtifactKey, chunks: Iterable[bytes], *, overwrite: bool = False
    ) -> ArtifactRef:
        buffer = bytearray()
        for chunk in chunks:
            buffer.extend(chunk)
            if len(buffer) > self._max_object_bytes:
                raise ArtifactTooLargeError(
                    f"artifact {key} exceeds the {self._max_object_bytes}-byte limit"
                )
        data = bytes(buffer)
        ref = ArtifactRef(key=key, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
        statement = insert(ArtifactObject).values(
            key=str(key), sha256=ref.sha256, size_bytes=ref.size_bytes, data=data
        )
        if overwrite:
            statement = statement.on_conflict_do_update(
                index_elements=[ArtifactObject.key],
                set_={
                    "sha256": statement.excluded.sha256,
                    "size_bytes": statement.excluded.size_bytes,
                    "data": statement.excluded.data,
                    "created_at": func.now(),
                },
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=[ArtifactObject.key])
        with self._engine.begin() as connection:
            written = connection.execute(statement.returning(ArtifactObject.key)).first()
        if written is None:  # ON CONFLICT DO NOTHING skipped the insert
            raise ArtifactExistsError(f"artifact already exists: {key}")
        return ref

    def read_bytes(self, key: ArtifactKey, *, max_bytes: int | None = None) -> bytes:
        limit = self._max_object_bytes if max_bytes is None else max_bytes
        with self._engine.connect() as connection:
            size = connection.scalar(
                select(ArtifactObject.size_bytes).where(ArtifactObject.key == str(key))
            )
            if size is None:
                raise ArtifactNotFoundError(f"artifact not found: {key}")
            if size > limit:
                raise ArtifactTooLargeError(f"artifact {key} exceeds the {limit}-byte read limit")
            data = connection.scalar(
                select(ArtifactObject.data).where(ArtifactObject.key == str(key))
            )
        if data is None:
            raise ArtifactNotFoundError(f"artifact not found: {key}")
        return bytes(data)

    @contextmanager
    def open_read(self, key: ArtifactKey) -> Iterator[IO[bytes]]:
        with io.BytesIO(self.read_bytes(key)) as handle:
            yield handle

    def stat(self, key: ArtifactKey) -> ArtifactRef:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(ArtifactObject.sha256, ArtifactObject.size_bytes).where(
                    ArtifactObject.key == str(key)
                )
            ).first()
        if row is None:
            raise ArtifactNotFoundError(f"artifact not found: {key}")
        return ArtifactRef(key=key, sha256=row.sha256, size_bytes=int(row.size_bytes))

    def exists(self, key: ArtifactKey) -> bool:
        with self._engine.connect() as connection:
            found = connection.scalar(
                select(ArtifactObject.key).where(ArtifactObject.key == str(key))
            )
        return found is not None

    def delete(self, key: ArtifactKey) -> bool:
        with self._engine.begin() as connection:
            removed = connection.execute(
                delete(ArtifactObject)
                .where(ArtifactObject.key == str(key))
                .returning(ArtifactObject.key)
            ).first()
        return removed is not None

    def probe(self) -> str:
        key = ArtifactKey(f"health-probes/{uuid.uuid4().hex}.bin")
        payload = os.urandom(64)
        try:
            ref = self.put_bytes(key, payload)
            if self.read_bytes(key) != payload or ref.size_bytes != len(payload):
                raise ArtifactError("artifact store probe read back different content")
        finally:
            self.delete(key)
        return "postgres"
