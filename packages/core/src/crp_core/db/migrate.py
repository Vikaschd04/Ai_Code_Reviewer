"""Programmatic Alembic access so every tool applies the same packaged migrations."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine
from sqlalchemy.pool import NullPool


def migrations_dir() -> Path:
    return Path(str(resources.files("crp_core.db").joinpath("migrations")))


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(migrations_dir()))
    # Alembic uses ConfigParser interpolation; escape '%' in URL-encoded passwords.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def head_revision() -> str:
    heads = ScriptDirectory.from_config(alembic_config("postgresql+psycopg://unused")).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"expected exactly one migration head, found {len(heads)}")
    return heads[0]


def current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


def downgrade(database_url: str, revision: str) -> None:
    command.downgrade(alembic_config(database_url), revision)


def check_models_match(database_url: str) -> None:
    """Raise if the ORM metadata differs from the migrated schema (``alembic check``)."""
    command.check(alembic_config(database_url))


def database_revision(database_url: str) -> str | None:
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            return current_revision(connection)
    finally:
        engine.dispose()
