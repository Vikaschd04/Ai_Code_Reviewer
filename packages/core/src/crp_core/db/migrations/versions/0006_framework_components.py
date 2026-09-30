"""Framework packs (P04): graph nodes may be framework components.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-30 19:10:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = "kind IN ('module', 'file', 'package', 'type', 'function', 'external')"
_AFTER = "kind IN ('module', 'file', 'package', 'type', 'function', 'external', 'component')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_graph_nodes_kind_valid"), "graph_nodes", type_="check")
    op.create_check_constraint(op.f("ck_graph_nodes_kind_valid"), "graph_nodes", _AFTER)


def downgrade() -> None:
    op.execute("DELETE FROM graph_nodes WHERE kind = 'component'")
    op.drop_constraint(op.f("ck_graph_nodes_kind_valid"), "graph_nodes", type_="check")
    op.create_check_constraint(op.f("ck_graph_nodes_kind_valid"), "graph_nodes", _BEFORE)
