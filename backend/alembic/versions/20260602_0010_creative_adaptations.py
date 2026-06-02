"""creative_adaptations: 创意迁移历史存档表

Revision ID: 0010_creative_adaptations
Revises: 0009_own_product_materials
Create Date: 2026-06-02

「创意迁移」每次生成（方向 + 可选脚本）都自动落库进历史，用户可手动删除，
避免花了钱的成品因刷新/离开页面丢失。一行 = 一次方向 run + 其最后一次脚本。
强绑 materials.id，素材删除时 CASCADE 清理。纯新增表，向前兼容、回滚走纯代码。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_creative_adaptations"
down_revision: Union[str, None] = "0009_own_product_materials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "creative_adaptations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "material_id",
            sa.Integer(),
            sa.ForeignKey("materials.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("our_product", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("directions", sa.JSON(), nullable=True),
        sa.Column("constraints_check", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("chosen_index", sa.Integer(), nullable=True),
        sa.Column("chosen_name", sa.String(length=200), nullable=True),
        sa.Column("script", sa.JSON(), nullable=True),
        sa.Column("script_model", sa.String(length=80), nullable=True),
        sa.Column("script_cost_usd", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("script_updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_creative_adaptations_material_id",
        "creative_adaptations",
        ["material_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_creative_adaptations_material_id", table_name="creative_adaptations")
    op.drop_table("creative_adaptations")
