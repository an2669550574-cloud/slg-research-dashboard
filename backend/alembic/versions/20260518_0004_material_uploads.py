"""materials: 支持上传文件（本地存储）—— 新增 source/file_* 列，url 改可空

Revision ID: 0004_material_uploads
Revises: 0003_rankings_meta
Create Date: 2026-05-18

素材库从「仅外链」升级为「外链 + 部门自有上传文件」。link 素材继续用 url；
upload 素材用 file_path/file_name/file_size/mime_type，url 置空，故 url 改可空。
存量行 source 回填为 'link'（server_default）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_material_uploads"
down_revision: Union[str, None] = "0003_rankings_meta"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite：batch 模式重建表以加列 / 改列可空
    with op.batch_alter_table("materials") as batch:
        batch.add_column(sa.Column("source", sa.String(20), nullable=False,
                                   server_default="link"))
        batch.add_column(sa.Column("file_path", sa.String(500), nullable=True))
        batch.add_column(sa.Column("file_name", sa.String(300), nullable=True))
        batch.add_column(sa.Column("file_size", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("mime_type", sa.String(100), nullable=True))
        batch.alter_column("url", existing_type=sa.String(1000), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("materials") as batch:
        batch.alter_column("url", existing_type=sa.String(1000), nullable=False)
        batch.drop_column("mime_type")
        batch.drop_column("file_size")
        batch.drop_column("file_name")
        batch.drop_column("file_path")
        batch.drop_column("source")
