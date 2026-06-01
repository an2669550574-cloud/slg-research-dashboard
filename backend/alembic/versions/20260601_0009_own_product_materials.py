"""own_product_materials: 自有产品素材表（喂给 AI 反推产品画像）

Revision ID: 0009_own_product_materials
Revises: 0008_own_products
Create Date: 2026-06-01

「我方产品」模块挂自有素材（宣传片/商店截图/商店描述），AI 据此反推产品
特点生成 brief 草稿。与竞品素材库（materials，强绑 app_id）刻意隔离。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_own_product_materials"
down_revision: Union[str, None] = "0008_own_products"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "own_product_materials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "own_product_id",
            sa.Integer(),
            sa.ForeignKey("own_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_type", sa.String(length=20), nullable=False),  # video/image/text
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("file_name", sa.String(length=300), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_own_product_materials_own_product_id",
        "own_product_materials",
        ["own_product_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_own_product_materials_own_product_id", table_name="own_product_materials")
    op.drop_table("own_product_materials")
