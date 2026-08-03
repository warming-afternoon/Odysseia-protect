"""add thread source metadata

Revision ID: d2a4f7c9b1e3
Revises: c84b8e9a2d11
Create Date: 2026-08-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2a4f7c9b1e3"
down_revision: Union[str, Sequence[str], None] = "c84b8e9a2d11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


source_status = sa.Enum(
    "unknown",
    "active",
    "deleted",
    name="source_status",
    native_enum=False,
)


def upgrade() -> None:
    with op.batch_alter_table("threads") as batch_op:
        batch_op.add_column(sa.Column("guild_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(
            sa.Column("public_thread_name", sa.String(length=100), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "source_status",
                source_status,
                nullable=False,
                server_default="unknown",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("threads") as batch_op:
        batch_op.drop_column("source_status")
        batch_op.drop_column("public_thread_name")
        batch_op.drop_column("guild_id")
