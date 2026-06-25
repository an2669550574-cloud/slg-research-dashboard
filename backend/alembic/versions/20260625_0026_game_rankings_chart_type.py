"""game_rankings: add chart_type + 五元组 unique（收入榜/下载榜并存）

Revision ID: 0026_rankings_chart_type
Revises: 0025_tag_option_products
Create Date: 2026-06-25

ADR 0001：榜单增加 chart_type 维度，并行采集下载/免费榜用于新品监测。
- 新增列 chart_type，server_default='grossing'：存量行（历史唯一的收入榜）回填 grossing。
- 唯一约束由 (app_id,date,country,platform) 扩为五元组（加 chart_type），让收入榜
  与下载榜在同一 (市场,日) 并存不撞。SQLite 经 batch 重建表实现。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0026_rankings_chart_type"
down_revision: Union[str, None] = "0025_tag_option_products"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("game_rankings") as batch:
        batch.add_column(sa.Column(
            "chart_type", sa.String(20),
            nullable=False, server_default="grossing"))
        batch.drop_constraint("uq_game_rankings_day_market", type_="unique")
        batch.create_unique_constraint(
            "uq_game_rankings_day_market",
            ["app_id", "date", "country", "platform", "chart_type"],
        )
        batch.create_index("ix_game_rankings_chart_type", ["chart_type"])


def downgrade() -> None:
    with op.batch_alter_table("game_rankings") as batch:
        batch.drop_index("ix_game_rankings_chart_type")
        batch.drop_constraint("uq_game_rankings_day_market", type_="unique")
        batch.create_unique_constraint(
            "uq_game_rankings_day_market",
            ["app_id", "date", "country", "platform"],
        )
        batch.drop_column("chart_type")
