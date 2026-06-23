"""tag_dimension_products：一级标签 ↔ 产品(app_id) 作用域名单

Revision ID: 0024_tag_dimension_products
Revises: 0023_publisher_ignores
Create Date: 2026-06-23

S1：维度级产品作用域。空名单 = 通用（所有产品可见）；非空 = 仅名单内产品可见。
纯新增表，旧代码忽略即可——回滚走纯代码（见 docs/ROLLBACK.md 判据）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0024_tag_dimension_products"
down_revision: Union[str, None] = "0023_publisher_ignores"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tag_dimension_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "dimension_id", sa.Integer(),
            sa.ForeignKey("tag_dimensions.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("app_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("dimension_id", "app_id", name="uq_tag_dim_product"),
    )
    op.create_index(
        "ix_tag_dim_products_app", "tag_dimension_products", ["app_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_tag_dim_products_app", table_name="tag_dimension_products")
    op.drop_table("tag_dimension_products")
