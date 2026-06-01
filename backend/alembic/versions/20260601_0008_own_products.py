"""own_products: 自家产品档案表

Revision ID: 0008_own_products
Revises: 0007_material_analysis_frames
Create Date: 2026-06-01

创意迁移的「自家产品 brief」从手输改为预存档案：建一张 own_products 表，
存命名好的 brief（题材/玩法/卖点/受众/差异化自由文本）+ is_default 标记。
前端管理页维护 1-2 条，迁移面板打开时默认带入。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_own_products"
down_revision: Union[str, None] = "0007_material_analysis_frames"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "own_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("own_products")
