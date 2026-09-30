"""Add global author collaborators.

Revision ID: a9c2e5f8b1d4
Revises: e4c6b8d0f2a1
"""

from alembic import op
import sqlalchemy as sa

revision = "a9c2e5f8b1d4"
down_revision = "e4c6b8d0f2a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "author_collaborators",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("collaborator_id", sa.BigInteger(), nullable=False),
        sa.Column("granted_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("author_id", "collaborator_id", name="uq_author_collaborator"),
    )


def downgrade() -> None:
    op.drop_table("author_collaborators")
