"""tag_option_products：二级标签 ↔ 产品(app_id) 作用域名单

Revision ID: 0025_tag_option_products
Revises: 0024_tag_dimension_products
Create Date: 2026-06-23

S2：选项级产品作用域。同 0024 套路——空名单 = 通用；非空 = 仅名单内产品可见。
（典型场景：「角色」维度共享，但 A 游戏的角色值只对 A 列出，与 B 不混。）
纯新增表，旧代码忽略即可——回滚走纯代码。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0025_tag_option_products"
down_revision: Union[str, None] = "0024_tag_dimension_products"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tag_option_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "option_id", sa.Integer(),
            sa.ForeignKey("tag_options.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("app_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("option_id", "app_id", name="uq_tag_opt_product"),
    )
    op.create_index(
        "ix_tag_opt_products_app", "tag_option_products", ["app_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_tag_opt_products_app", table_name="tag_option_products")
    op.drop_table("tag_option_products")
