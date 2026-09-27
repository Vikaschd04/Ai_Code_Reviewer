"""Migration tests against a real PostgreSQL 18 instance (no mocks)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import psycopg
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from crp_core.config import Settings
from crp_core.db import migrate
from crp_core.db.identity import ensure_local_identity
from crp_core.db.session import create_engine_from_settings, create_session_factory, transaction

pytestmark = pytest.mark.integration

BOUNDARY_TABLES = {
    "workspaces",
    "users",
    "memberships",
    "projects",
    "sources",
    "snapshots",
    "scans",
}


def _tables(url: str) -> set[str]:
    engine = create_engine(url, poolclass=NullPool)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_check_downgrade_roundtrip(empty_database_url: str) -> None:
    assert migrate.database_revision(empty_database_url) is None
    migrate.upgrade(empty_database_url)
    assert migrate.database_revision(empty_database_url) == migrate.head_revision()
    assert _tables(empty_database_url) >= BOUNDARY_TABLES
    migrate.check_models_match(empty_database_url)  # raises if ORM and migrations drift

    migrate.downgrade(empty_database_url, "base")
    assert _tables(empty_database_url) & BOUNDARY_TABLES == set()
    migrate.upgrade(empty_database_url)
    assert migrate.database_revision(empty_database_url) == migrate.head_revision()


def _seed_two_projects(conn: psycopg.Connection[tuple[object, ...]]) -> dict[str, uuid.UUID]:
    ids = {name: uuid.uuid4() for name in ("ws", "p1", "p2", "src1", "snap1")}
    conn.execute("INSERT INTO workspaces (id, slug, name) VALUES (%s, 'w', 'W')", (ids["ws"],))
    for key, slug in (("p1", "one"), ("p2", "two")):
        conn.execute(
            "INSERT INTO projects (id, workspace_id, slug, name, origin) "
            "VALUES (%s, %s, %s, %s, 'user')",
            (ids[key], ids["ws"], slug, slug),
        )
    conn.execute(
        "INSERT INTO sources (id, workspace_id, project_id, mode, display_name) "
        "VALUES (%s, %s, %s, 'zip_upload', 'upload')",
        (ids["src1"], ids["ws"], ids["p1"]),
    )
    conn.execute(
        "INSERT INTO snapshots (id, workspace_id, project_id, source_id, capture_status) "
        "VALUES (%s, %s, %s, %s, 'PENDING')",
        (ids["snap1"], ids["ws"], ids["p1"], ids["src1"]),
    )
    return ids


def _connect(url: str) -> psycopg.Connection[tuple[object, ...]]:
    return psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True)


def test_scan_cannot_reference_snapshot_of_another_project(database_url: str) -> None:
    with _connect(database_url) as conn:
        ids = _seed_two_projects(conn)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO scans (workspace_id, project_id, snapshot_id, mode, policy_version, "
                "idempotency_key, state, id) VALUES (%s, %s, %s, 'baseline', 'v1', 'key-12345', "
                "'QUEUED', %s)",
                (ids["ws"], ids["p2"], ids["snap1"], uuid.uuid4()),
            )


def test_snapshot_constraints_enforce_identity_and_state(database_url: str) -> None:
    with _connect(database_url) as conn:
        ids = _seed_two_projects(conn)
        base = (
            "INSERT INTO snapshots (id, workspace_id, project_id, source_id, capture_status, "
            "manifest_sha256, frozen_at, git_commit) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        )
        scope = (ids["ws"], ids["p1"], ids["src1"])
        with pytest.raises(psycopg.errors.CheckViolation):  # frozen needs manifest + time
            conn.execute(base, (uuid.uuid4(), *scope, "FROZEN", None, None, None))
        with pytest.raises(psycopg.errors.CheckViolation):  # manifest must be a sha256
            conn.execute(base, (uuid.uuid4(), *scope, "PENDING", "not-a-hash", None, None))
        with pytest.raises(psycopg.errors.CheckViolation):  # never invent short commit ids
            conn.execute(base, (uuid.uuid4(), *scope, "PENDING", None, None, "abc123"))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):  # source from another project
            conn.execute(
                base, (uuid.uuid4(), ids["ws"], ids["p2"], ids["src1"], "PENDING", None, None, None)
            )
        conn.execute(base, (uuid.uuid4(), *scope, "FROZEN", "a" * 64, "2026-09-26T00:00Z", None))


def test_scan_state_and_idempotency_constraints(database_url: str) -> None:
    with _connect(database_url) as conn:
        ids = _seed_two_projects(conn)
        insert = (
            "INSERT INTO scans (id, workspace_id, project_id, snapshot_id, mode, policy_version, "
            "idempotency_key, state) VALUES (%s, %s, %s, %s, 'baseline', 'v1', %s, %s)"
        )
        scope = (ids["ws"], ids["p1"], ids["snap1"])
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(insert, (uuid.uuid4(), *scope, "key-12345", "DONE"))
        conn.execute(insert, (uuid.uuid4(), *scope, "key-12345", "QUEUED"))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(insert, (uuid.uuid4(), *scope, "key-12345", "QUEUED"))


async def test_local_identity_provisioning_is_idempotent(
    database_url: str, make_settings: Callable[..., Settings]
) -> None:
    settings = make_settings(database_url=database_url)
    engine = create_engine_from_settings(settings)
    factory = create_session_factory(engine)
    try:
        async with transaction(factory) as session:
            first = await ensure_local_identity(session)
        async with transaction(factory) as session:
            second = await ensure_local_identity(session)
        assert first == second
        async with engine.connect() as conn:
            count = await conn.scalar(text("SELECT count(*) FROM memberships"))
            role = await conn.scalar(text("SELECT role FROM memberships"))
        assert (count, role) == (1, "owner")
        with pytest.raises(IntegrityError):
            async with transaction(factory) as session:
                await session.execute(
                    text("INSERT INTO workspaces (id, slug, name) VALUES (:id, 'Bad Slug', 'x')"),
                    {"id": uuid.uuid4()},
                )
    finally:
        await engine.dispose()


def _seed_finding_chain(conn: psycopg.Connection[tuple[object, ...]]) -> dict[str, uuid.UUID]:
    ids = _seed_two_projects(conn)
    ids.update({k: uuid.uuid4() for k in ("file", "scan", "run", "finding")})
    conn.execute(
        "INSERT INTO file_entries (id, workspace_id, project_id, snapshot_id, path, disposition, "
        "blob_sha256) VALUES (%s, %s, %s, %s, 'A.java', 'ANALYZABLE', %s)",
        (ids["file"], ids["ws"], ids["p1"], ids["snap1"], "b" * 64),
    )
    conn.execute(
        "INSERT INTO scans (id, workspace_id, project_id, snapshot_id, mode, policy_version, "
        "idempotency_key, state) VALUES (%s, %s, %s, %s, 'baseline', 'v1', 'key-12345', 'QUEUED')",
        (ids["scan"], ids["ws"], ids["p1"], ids["snap1"]),
    )
    conn.execute(
        "INSERT INTO engine_runs (id, scan_id, engine, state) VALUES (%s, %s, 'pmd', 'SUCCEEDED')",
        (ids["run"], ids["scan"]),
    )
    return ids


_FINDING = (
    "INSERT INTO findings (id, workspace_id, project_id, snapshot_id, scan_id, engine_run_id, "
    "file_entry_id, fingerprint, engine, engine_version, rule_id, severity, category, "
    "confidence, title, message, start_line, end_line, status{extra}) VALUES (%s, %s, %s, %s, "
    "%s, %s, %s, %s, 'pmd', '7', 'R', 'low', %s, 'deterministic_rule', 't', 'm', %s, %s, "
    "'OPEN'{values})"
)


def test_0003_backfills_correlation_and_allows_lineless_dependency_findings(
    empty_database_url: str,
) -> None:
    migrate.upgrade(empty_database_url, "0002")
    with _connect(empty_database_url) as conn:
        ids = _seed_finding_chain(conn)
        scope = (ids["ws"], ids["p1"], ids["snap1"], ids["scan"], ids["run"], ids["file"])
        conn.execute(
            _FINDING.format(extra="", values=""),
            (ids["finding"], *scope, "c" * 64, "correctness", 3, 3),
        )
    migrate.upgrade(empty_database_url)
    with _connect(empty_database_url) as conn:
        row = conn.execute(
            "SELECT correlation_key, anchor_kind FROM findings WHERE id = %s", (ids["finding"],)
        ).fetchone()
        assert row == ("c" * 64, "source_span")
        dep = _FINDING.format(extra=", anchor_kind, correlation_key", values=", %s, %s")
        conn.execute(
            dep,
            (uuid.uuid4(), *scope, "d" * 64, "dependencies", None, None, "dependency", "d" * 64),
        )
        with pytest.raises(psycopg.errors.CheckViolation):  # a source span needs lines
            conn.execute(
                dep,
                (uuid.uuid4(), *scope, "e" * 64, "security", None, None, "source_span", "e" * 64),
            )
    migrate.downgrade(empty_database_url, "0002")  # drops rows 0002 cannot represent
    with _connect(empty_database_url) as conn:
        assert conn.execute("SELECT count(*) FROM findings").fetchone() == (1,)


def test_issue_exception_constraints(database_url: str) -> None:
    with _connect(database_url) as conn:
        ids = _seed_two_projects(conn)
        insert = (
            "INSERT INTO issues (id, workspace_id, project_id, fingerprint, engine, rule_id, "
            "path, title, severity, category, status, recheck_state, exception_reason, "
            "exception_expires_at) VALUES (%s, %s, %s, %s, 'pmd', 'R', 'A.java', 't', 'low', "
            "'correctness', %s, 'VERIFIED_PRESENT', %s, %s)"
        )
        scope = (ids["ws"], ids["p1"])
        with pytest.raises(psycopg.errors.CheckViolation):  # accepted risk needs a reason
            conn.execute(insert, (uuid.uuid4(), *scope, "a" * 64, "ACCEPTED_RISK", " ", None))
        with pytest.raises(psycopg.errors.CheckViolation):  # ... and an expiry
            conn.execute(insert, (uuid.uuid4(), *scope, "a" * 64, "ACCEPTED_RISK", "ok", None))
        with pytest.raises(psycopg.errors.CheckViolation):  # false positive needs a reason
            conn.execute(insert, (uuid.uuid4(), *scope, "a" * 64, "FALSE_POSITIVE", None, None))
        conn.execute(
            insert, (uuid.uuid4(), *scope, "a" * 64, "ACCEPTED_RISK", "ok", "2027-01-01T00:00Z")
        )
        with pytest.raises(psycopg.errors.UniqueViolation):  # one issue per fingerprint
            conn.execute(insert, (uuid.uuid4(), *scope, "a" * 64, "OPEN", None, None))


def test_graph_anchors_must_belong_to_the_build_snapshot(database_url: str) -> None:
    with _connect(database_url) as conn:
        ids = _seed_finding_chain(conn)
        other_snap, build = uuid.uuid4(), uuid.uuid4()
        conn.execute(
            "INSERT INTO snapshots (id, workspace_id, project_id, source_id, capture_status) "
            "VALUES (%s, %s, %s, %s, 'PENDING')",
            (other_snap, ids["ws"], ids["p1"], ids["src1"]),
        )
        conn.execute(
            "INSERT INTO graph_builds (id, workspace_id, project_id, snapshot_id, state, "
            "is_current, extractor) VALUES (%s, %s, %s, %s, 'SUCCEEDED', true, 'x')",
            (build, ids["ws"], ids["p1"], other_snap),
        )
        node = (
            "INSERT INTO graph_nodes (workspace_id, project_id, snapshot_id, build_id, kind, key, "
            "label, file_entry_id) VALUES (%s, %s, %s, %s, 'file', %s, 'A.java', %s)"
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):  # file from another snapshot
            conn.execute(node, (ids["ws"], ids["p1"], other_snap, build, "f:A", ids["file"]))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):  # build from another snapshot
            conn.execute(node, (ids["ws"], ids["p1"], ids["snap1"], build, "f:A", ids["file"]))
        with pytest.raises(psycopg.errors.UniqueViolation):  # one current build per snapshot
            conn.execute(
                "INSERT INTO graph_builds (workspace_id, project_id, snapshot_id, state, "
                "is_current, extractor, id) VALUES (%s, %s, %s, 'FAILED', true, 'x', %s)",
                (ids["ws"], ids["p1"], other_snap, uuid.uuid4()),
            )
