"""tag_taxonomy: 结构化标签库三表（一级框架 / 二级值 / 素材关联）

Revision ID: 0011_tag_taxonomy
Revises: 0010_creative_adaptations
Create Date: 2026-06-02

竞品素材库的自定义标签归类分析：一级标签(tag_dimensions) + 二级标签(tag_options) +
素材↔标签 junction(material_tag_values)。一级标签按 value_type 分 text(枚举二级) /
date(选日期，如「投放时间」)。与扁平 materials.tags / analysis_tags 并存不冲突。
纯新增表，向前兼容、回滚走纯代码。P1 只建表 + 标签库 CRUD；打标签 / 筛选 / 聚合后续期。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_tag_taxonomy"
down_revision: Union[str, None] = "0010_creative_adaptations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tag_dimensions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("value_type", sa.String(length=10), nullable=False, server_default="text"),
        sa.Column("material_type", sa.String(length=50), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allow_multi", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "tag_options",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "dimension_id", sa.Integer(),
            sa.ForeignKey("tag_dimensions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("value", sa.String(length=50), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_tag_options_dimension_id", "tag_options", ["dimension_id"])

    op.create_table(
        "material_tag_values",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "material_id", sa.Integer(),
            sa.ForeignKey("materials.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "dimension_id", sa.Integer(),
            sa.ForeignKey("tag_dimensions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "option_id", sa.Integer(),
            sa.ForeignKey("tag_options.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("value", sa.String(length=50), nullable=True),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_material_tag_values_material_id", "material_tag_values", ["material_id"])
    op.create_index("ix_material_tag_values_dimension_id", "material_tag_values", ["dimension_id"])
    op.create_index("ix_material_tag_values_value", "material_tag_values", ["value"])
    op.create_index("ix_material_tag_values_value_date", "material_tag_values", ["value_date"])


def downgrade() -> None:
    op.drop_index("ix_material_tag_values_value_date", table_name="material_tag_values")
    op.drop_index("ix_material_tag_values_value", table_name="material_tag_values")
    op.drop_index("ix_material_tag_values_dimension_id", table_name="material_tag_values")
    op.drop_index("ix_material_tag_values_material_id", table_name="material_tag_values")
    op.drop_table("material_tag_values")
    op.drop_index("ix_tag_options_dimension_id", table_name="tag_options")
    op.drop_table("tag_options")
    op.drop_table("tag_dimensions")
