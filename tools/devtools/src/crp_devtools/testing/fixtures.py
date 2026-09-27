"""Pytest plugin providing *real* PostgreSQL and Temporal instances for integration tests.

Each test session initializes a throwaway PostgreSQL cluster and Temporal dev server on free
loopback ports. Migrated databases are cloned per test from a template. Missing binaries make the
integration tests error with an explicit message; they are never silently skipped or mocked.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from crp_core.config import Settings
from crp_core.db import migrate
from crp_core.local_secrets import generate_token, write_secret_file
from crp_devtools.infra import PostgresCluster, TemporalDevServer, free_port

TEMPLATE_DB = "crp_template"


@pytest.fixture(scope="session")
def pg_cluster(tmp_path_factory: pytest.TempPathFactory) -> Iterator[PostgresCluster]:
    base = tmp_path_factory.mktemp("pg")
    cluster = PostgresCluster(
        data_dir=base / "data",
        port=free_port(),
        password=generate_token(),
        log_file=base / "pg.log",
    )
    cluster.init()
    cluster.start()
    try:
        yield cluster
    finally:
        cluster.stop()


@pytest.fixture(scope="session")
def migrated_template(pg_cluster: PostgresCluster) -> str:
    pg_cluster.create_database(TEMPLATE_DB)
    migrate.upgrade(pg_cluster.url(TEMPLATE_DB))
    return TEMPLATE_DB


@pytest.fixture
def database_url(pg_cluster: PostgresCluster, migrated_template: str) -> Iterator[str]:
    """A fresh database cloned from the migrated template."""
    name = f"t_{uuid.uuid4().hex[:16]}"
    pg_cluster.create_database(name, template=migrated_template)
    try:
        yield pg_cluster.url(name)
    finally:
        pg_cluster.drop_database(name)


@pytest.fixture
def empty_database_url(pg_cluster: PostgresCluster) -> Iterator[str]:
    """A fresh database with no schema, for migration tests."""
    name = f"e_{uuid.uuid4().hex[:16]}"
    pg_cluster.create_database(name)
    try:
        yield pg_cluster.url(name)
    finally:
        pg_cluster.drop_database(name)


@pytest.fixture(scope="session")
def temporal_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TemporalDevServer]:
    base = tmp_path_factory.mktemp("temporal")
    server = TemporalDevServer(port=free_port(), log_file=base / "temporal.log")
    server.start(detach=False)
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "secrets" / "local-api-token"
    write_secret_file(path, generate_token())
    return path


@pytest.fixture
def make_settings(tmp_path: Path, token_file: Path) -> Callable[..., Settings]:
    """Build validated test settings; callers override dependency addresses as needed."""

    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "environment": "test",
            "database_url": "postgresql+psycopg://nobody:none@127.0.0.1:1/none",
            "local_token_file": token_file,
            "artifact_root": tmp_path / "artifacts",
            "temporal_address": "127.0.0.1:1",
            "temporal_task_queue": f"crp-test-{uuid.uuid4().hex[:8]}",
            "temporal_connect_timeout_seconds": 2.0,
            "readiness_check_timeout_seconds": 5.0,
            "log_format": "text",
        }
        values.update(overrides)
        return Settings(**values)

    return factory
