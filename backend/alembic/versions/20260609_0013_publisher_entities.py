"""publisher_entities: 厂商主体三表（主体 / 海外发行马甲 / 单品 app_id 钉）

Revision ID: 0013_publisher_entities
Revises: 0012_tag_analysis
Create Date: 2026-06-09

把原先硬编码在 slg_publishers.py 的「SLG 发行商白名单 + 关注 app_id」升格为 DB
一等实体：publisher_entities(主体) + publisher_aliases(海外发行马甲，token 匹配) +
publisher_app_ids(多品类大厂单品精确钉)。is_slg 判定改以本三表为唯一数据源
（运行时走内存索引）。「主体→旗下产品」是查询态聚合，不在 game_rankings 加外键。
纯新增表，向前兼容、回滚走纯代码；起步种子由 scheduler.seed_publishers_if_empty 灌入。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_publisher_entities"
down_revision: Union[str, None] = "0012_tag_analysis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "publisher_entities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("name_en", sa.String(length=200), nullable=True),
        sa.Column("hq_region", sa.String(length=50), nullable=True),
        sa.Column("is_slg", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("brief", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "publisher_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entity_id", sa.Integer(),
            sa.ForeignKey("publisher_entities.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("keyword", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_publisher_aliases_entity_id", "publisher_aliases", ["entity_id"])

    op.create_table(
        "publisher_app_ids",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entity_id", sa.Integer(),
            sa.ForeignKey("publisher_entities.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("app_id", sa.String(length=100), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_publisher_app_ids_entity_id", "publisher_app_ids", ["entity_id"])
    op.create_index("ix_publisher_app_ids_app_id", "publisher_app_ids", ["app_id"])


def downgrade() -> None:
    op.drop_index("ix_publisher_app_ids_app_id", table_name="publisher_app_ids")
    op.drop_index("ix_publisher_app_ids_entity_id", table_name="publisher_app_ids")
    op.drop_table("publisher_app_ids")
    op.drop_index("ix_publisher_aliases_entity_id", table_name="publisher_aliases")
    op.drop_table("publisher_aliases")
    op.drop_table("publisher_entities")
