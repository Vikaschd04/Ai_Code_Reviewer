"""AI connections (ADR 0025): each project's AI policy names the connection that receives its
code (``primary`` or ``alt``), and each AI run records the connection it used, so budgets and
usage are counted per connection.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-14 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VALID = "connection IN ('primary', 'alt')"


def upgrade() -> None:
    for table in ("project_ai_policies", "ai_runs"):
        op.add_column(
            table,
            sa.Column("connection", sa.String(length=16), nullable=False, server_default="primary"),
        )
        op.create_check_constraint(op.f(f"ck_{table}_connection_valid"), table, _VALID)


def downgrade() -> None:
    for table in ("ai_runs", "project_ai_policies"):
        op.drop_constraint(op.f(f"ck_{table}_connection_valid"), table, type_="check")
        op.drop_column(table, "connection")
