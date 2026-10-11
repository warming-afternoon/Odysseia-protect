"""新增独立资源作者备注，保留历史资源字段与数据。"""

from alembic import op
import sqlalchemy as sa

revision = "b6d8f0a2c4e1"
down_revision = "a9c2e5f8b1d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增不限制长度的可空作者备注列。"""
    # 旧资源保持无备注，不改写密码或描述。
    op.add_column("resources", sa.Column("author_note", sa.Text(), nullable=True))


def downgrade() -> None:
    """仅删除作者备注列并保留其余资源数据。"""
    # SQLite 使用批量表变更确保兼容删除列操作。
    with op.batch_alter_table("resources") as batch_op:
        batch_op.drop_column("author_note")
