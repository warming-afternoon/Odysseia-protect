"""add download panel message id

Revision ID: 91f0d81ae4b2
Revises: 5e6f70913e2c
Create Date: 2026-07-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "91f0d81ae4b2"
down_revision: Union[str, Sequence[str], None] = "5e6f70913e2c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("threads") as batch_op:
        batch_op.add_column(
            sa.Column("download_panel_message_id", sa.BigInteger(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("threads") as batch_op:
        batch_op.drop_column("download_panel_message_id")
