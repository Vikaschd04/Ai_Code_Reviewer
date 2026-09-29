"""Artifact objects stored in PostgreSQL for lite deployments without a persistent disk.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-29 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifact_objects",
        sa.Column("key", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_artifact_objects_sha256_format")
        ),
        sa.CheckConstraint(
            "size_bytes = octet_length(data)",
            name=op.f("ck_artifact_objects_size_matches_data"),
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_artifact_objects")),
    )


def downgrade() -> None:
    op.drop_table("artifact_objects")
