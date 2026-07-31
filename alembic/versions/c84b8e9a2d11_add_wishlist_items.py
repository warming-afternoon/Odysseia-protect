"""add wishlist items

Revision ID: c84b8e9a2d11
Revises: 91f0d81ae4b2
Create Date: 2026-07-31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c84b8e9a2d11"
down_revision: Union[str, Sequence[str], None] = "91f0d81ae4b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "has_agreed_to_wishlist_policy",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    op.create_table(
        "wishlist_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["resources.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "resource_id",
            name="uq_wishlist_user_resource",
        ),
    )
    op.create_index(
        "ix_wishlist_user_created",
        "wishlist_items",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_wishlist_user_created", table_name="wishlist_items")
    op.drop_table("wishlist_items")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("has_agreed_to_wishlist_policy")
