"""add dynamic traceability and verification jobs

Revision ID: e4c6b8d0f2a1
Revises: d2a4f7c9b1e3
Create Date: 2026-08-31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4c6b8d0f2a1"
down_revision: Union[str, Sequence[str], None] = "d2a4f7c9b1e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("resources"):
        with op.batch_alter_table("resources") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "trace_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )

    op.create_table(
        "trace_verification_jobs",
        sa.Column("report_id", sa.String(length=32), nullable=False),
        sa.Column("requester_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("input_filename", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=True),
        sa.Column("report_object_key", sa.String(length=512), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("report_id"),
    )
    op.create_index(
        "ix_trace_jobs_requester_created",
        "trace_verification_jobs",
        ["requester_id", "created_at"],
    )
    op.create_index(
        "ix_trace_jobs_expires",
        "trace_verification_jobs",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_trace_jobs_expires", table_name="trace_verification_jobs")
    op.drop_index(
        "ix_trace_jobs_requester_created", table_name="trace_verification_jobs"
    )
    op.drop_table("trace_verification_jobs")
    bind = op.get_bind()
    if sa.inspect(bind).has_table("resources"):
        with op.batch_alter_table("resources") as batch_op:
            batch_op.drop_column("trace_enabled")
