"""AI fix suggestions in fix workspaces (P08 slice 4): AI runs of kind ``fix`` linked to a
workspace and to the exact text of the file their candidates were made for.

The link is ``SET NULL`` on workspace deletion so AI usage accounting (``ai_calls``) survives.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-08 23:38:24.727339
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS_BEFORE = "kind IN ('question', 'finding_review', 'file_review')"
_KINDS_AFTER = "kind IN ('question', 'finding_review', 'file_review', 'fix')"
_TARGET_SHA = "target_sha256 IS NULL OR target_sha256 ~ '^[0-9a-f]{64}$'"


def _kinds(check: str) -> None:
    op.drop_constraint(op.f("ck_ai_runs_kind_valid"), "ai_runs", type_="check")
    op.create_check_constraint(op.f("ck_ai_runs_kind_valid"), "ai_runs", check)


def upgrade() -> None:
    op.add_column("ai_runs", sa.Column("change_set_id", sa.Uuid(), nullable=True))
    op.add_column("ai_runs", sa.Column("target_sha256", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_ai_runs_change_set_id"), "ai_runs", ["change_set_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_ai_runs_change_set_id_change_sets"),
        "ai_runs",
        "change_sets",
        ["change_set_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(op.f("ck_ai_runs_target_sha256_format"), "ai_runs", _TARGET_SHA)
    _kinds(_KINDS_AFTER)


def downgrade() -> None:
    op.execute("DELETE FROM ai_runs WHERE kind = 'fix'")
    _kinds(_KINDS_BEFORE)
    op.drop_constraint(op.f("ck_ai_runs_target_sha256_format"), "ai_runs", type_="check")
    op.drop_constraint(op.f("fk_ai_runs_change_set_id_change_sets"), "ai_runs", type_="foreignkey")
    op.drop_index(op.f("ix_ai_runs_change_set_id"), table_name="ai_runs")
    op.drop_column("ai_runs", "target_sha256")
    op.drop_column("ai_runs", "change_set_id")
