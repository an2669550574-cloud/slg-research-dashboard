"""tag packs：标签包（把一级标签分组成自定义大类）

四张新表、零改动现有表——不建包时系统行为与之前完全一致，回滚 = drop 四表：
- tag_packs             包本体（自定义命名 + 排序）
- tag_pack_dimensions   包 ↔ 一级标签 多对多（一个维度可同属多个包：包是视图不是分区）
- tag_pack_products     包的产品作用域（沿用 0024/0025 范式：空名单 = 通用）
- tag_pack_settings     产品级总开关（素材库是否启用包视图；无记录 = 默认关）

Revision ID: 0046_tag_packs
Revises: 0045_group_label
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0046_tag_packs"
down_revision: Union[str, None] = "0045_group_label"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tag_packs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "tag_pack_dimensions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack_id", sa.Integer(),
                  sa.ForeignKey("tag_packs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dimension_id", sa.Integer(),
                  sa.ForeignKey("tag_dimensions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tag_pack_dimensions_pack_id", "tag_pack_dimensions", ["pack_id"])
    op.create_index("ix_tag_pack_dimensions_dimension_id", "tag_pack_dimensions", ["dimension_id"])
    op.create_table(
        "tag_pack_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack_id", sa.Integer(),
                  sa.ForeignKey("tag_packs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("app_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tag_pack_products_pack_id", "tag_pack_products", ["pack_id"])
    op.create_index("ix_tag_pack_products_app_id", "tag_pack_products", ["app_id"])
    op.create_table(
        "tag_pack_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_id", sa.String(length=100), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tag_pack_settings")
    op.drop_index("ix_tag_pack_products_app_id", table_name="tag_pack_products")
    op.drop_index("ix_tag_pack_products_pack_id", table_name="tag_pack_products")
    op.drop_table("tag_pack_products")
    op.drop_index("ix_tag_pack_dimensions_dimension_id", table_name="tag_pack_dimensions")
    op.drop_index("ix_tag_pack_dimensions_pack_id", table_name="tag_pack_dimensions")
    op.drop_table("tag_pack_dimensions")
    op.drop_table("tag_packs")
