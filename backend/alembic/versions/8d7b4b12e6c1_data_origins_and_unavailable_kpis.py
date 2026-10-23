"""data origins and unavailable KPIs

Revision ID: 8d7b4b12e6c1
Revises: b43c4cc3a409
Create Date: 2026-10-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8d7b4b12e6c1"
down_revision: Union[str, None] = "b43c4cc3a409"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("pnl_items", "balance_sheet_items", "cash_flow_items"):
        op.add_column(
            table,
            sa.Column(
                "origin",
                sa.String(length=20),
                nullable=False,
                server_default="reported",
            ),
        )
        op.execute(f"UPDATE {table} SET origin = 'derived' WHERE line_item = 'EBITDA'")
    op.alter_column("kpis", "value", existing_type=sa.Numeric(15, 4), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE kpis SET value = 0 WHERE value IS NULL")
    op.alter_column("kpis", "value", existing_type=sa.Numeric(15, 4), nullable=False)
    for table in ("cash_flow_items", "balance_sheet_items", "pnl_items"):
        op.drop_column(table, "origin")
