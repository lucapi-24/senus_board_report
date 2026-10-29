"""document narrative

Revision ID: e1f4a7c9d2b3
Revises: 8d7b4b12e6c1
Create Date: 2026-10-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1f4a7c9d2b3"
down_revision: Union[str, None] = "8d7b4b12e6c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("narrative_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "narrative_json")
