"""Advisor runs: AI runs of kind ``advisor`` with the evidence pack (recommendations, numbered
facts, targets) frozen at the request, so every plan is checked against fixed facts.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-10 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS_BEFORE = "kind IN ('question', 'finding_review', 'file_review', 'fix')"
_KINDS_AFTER = "kind IN ('question', 'finding_review', 'file_review', 'fix', 'advisor')"


def _kinds(check: str) -> None:
    op.drop_constraint(op.f("ck_ai_runs_kind_valid"), "ai_runs", type_="check")
    op.create_check_constraint(op.f("ck_ai_runs_kind_valid"), "ai_runs", check)


def upgrade() -> None:
    op.add_column(
        "ai_runs",
        sa.Column(
            "context",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    _kinds(_KINDS_AFTER)


def downgrade() -> None:
    op.execute("DELETE FROM ai_runs WHERE kind = 'advisor'")
    _kinds(_KINDS_BEFORE)
    op.drop_column("ai_runs", "context")
