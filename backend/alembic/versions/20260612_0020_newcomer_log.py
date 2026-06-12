"""market_newcomer_log：全市场新面孔检出沉淀 + 免费源富化

Revision ID: 0020_newcomer_log
Revises: 0019_gp_platform
Create Date: 2026-06-12

新品监测 v2：检出不再只活在「本期」内存里——定时同步检出即落库（每
combo×app_id 唯一，首报一次），页面可回看 30/90 天历史；落库时用免费源
（iOS=iTunes lookup / Android=GP 页 JSON-LD，零 ST 配额）富化上架日/子品类/
评分/价格/描述/截图。纯新增表、回滚走纯代码。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020_newcomer_log"
down_revision: Union[str, None] = "0019_gp_platform"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_newcomer_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("country", sa.String(length=10), nullable=False),
        sa.Column("platform", sa.String(length=10), nullable=False),
        sa.Column("app_id", sa.String(length=200), nullable=False),
        sa.Column("as_of", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("publisher", sa.String(length=300), nullable=True),
        sa.Column("icon_url", sa.String(length=1000), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("revenue", sa.Float(), nullable=True),
        sa.Column("is_slg", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("first_detected_at", sa.DateTime(), nullable=False),
        # 免费源富化（iOS=iTunes lookup / Android=GP 页 JSON-LD），失败留 NULL
        sa.Column("store_url", sa.String(length=1000), nullable=True),
        sa.Column("release_date", sa.String(length=30), nullable=True),
        sa.Column("genre", sa.String(length=100), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("rating_count", sa.Integer(), nullable=True),
        sa.Column("price", sa.String(length=50), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("screenshot_urls", sa.Text(), nullable=True),
        sa.Column("enrich_source", sa.String(length=20), nullable=True),
        sa.Column("enriched_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("country", "platform", "app_id", name="uq_newcomer_per_combo"),
    )
    op.create_index("ix_newcomer_log_detected", "market_newcomer_log", ["first_detected_at"])


def downgrade() -> None:
    op.drop_index("ix_newcomer_log_detected", table_name="market_newcomer_log")
    op.drop_table("market_newcomer_log")
