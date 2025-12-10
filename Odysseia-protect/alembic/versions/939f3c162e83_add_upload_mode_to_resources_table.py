"""add upload_mode to resources table

Revision ID: 939f3c162e83
Revises:
Create Date: 2025-12-10 00:53:26.022498

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "939f3c162e83"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    将 threads.mode 的数据迁移到新的 resources.upload_mode 列，
    然后删除旧的 threads.mode 列。
    """
    # 步骤 1: 创建一个新的 upload_mode 列，暂时允许为空
    upload_mode_enum = sa.Enum("SECURE", "NORMAL", name="uploadmode")
    op.add_column(
        "resources", sa.Column("upload_mode", upload_mode_enum, nullable=True)
    )

    # 步骤 2: 将 threads.mode 的数据迁移到 resources.upload_mode
    # 这里我们使用了一个 UPDATE FROM 子查询 (SQLite 特定语法)
    op.execute("""
        UPDATE resources
        SET upload_mode = (
            SELECT mode FROM threads WHERE threads.id = resources.thread_id
        )
    """)

    # 步骤 3: 确保所有行都被填充后，将新列设置为 NOT NULL
    # 对于 SQLite，这需要批量操作
    with op.batch_alter_table("resources", schema=None) as batch_op:
        batch_op.alter_column(
            "upload_mode", existing_type=upload_mode_enum, nullable=False
        )

    # 步骤 4: 从 threads 表中删除旧的 mode 列
    with op.batch_alter_table("threads", schema=None) as batch_op:
        batch_op.drop_column("mode")


def downgrade() -> None:
    """
    逆向操作：重新创建 threads.mode 列，
    并将 resources.upload_mode 的数据迁回，然后删除新列。
    """
    # 步骤 1: 重新在 threads 表中添加 mode 列，暂时允许为空
    op.add_column("threads", sa.Column("mode", sa.String(length=10), nullable=True))

    # 步骤 2: 将 resources.upload_mode 的数据迁回到 threads.mode
    # 注意：这里假设一个 thread 里的所有 resource 模式都相同
    op.execute("""
        UPDATE threads
        SET mode = (
            SELECT upload_mode FROM resources WHERE resources.thread_id = threads.id LIMIT 1
        )
    """)

    # 步骤 3: 将 threads.mode 设置为 NOT NULL
    with op.batch_alter_table("threads", schema=None) as batch_op:
        batch_op.alter_column(
            "mode", existing_type=sa.String(length=10), nullable=False
        )

    # 步骤 4: 从 resources 表中删除 upload_mode 列
    with op.batch_alter_table("resources", schema=None) as batch_op:
        batch_op.drop_column("upload_mode")
