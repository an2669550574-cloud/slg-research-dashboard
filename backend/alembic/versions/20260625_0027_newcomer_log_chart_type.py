"""market_newcomer_log: add chart_type + 四元组 unique（收入榜/下载榜各自留底）

Revision ID: 0027_newcomer_log_chart_type
Revises: 0026_rankings_chart_type
Create Date: 2026-06-25

ADR 0001 切片 2：下载榜新品也进检出沉淀。
- 新增列 chart_type，server_default='grossing'：存量行回填 grossing。
- 唯一约束 (country,platform,app_id) → 四元组（加 chart_type），让同一 app 在收入榜
  与下载榜各留一条检出记录、互不覆盖。SQLite 经 batch 重建表。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0027_newcomer_log_chart_type"
down_revision: Union[str, None] = "0026_rankings_chart_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("market_newcomer_log") as batch:
        batch.add_column(sa.Column(
            "chart_type", sa.String(20),
            nullable=False, server_default="grossing"))
        batch.drop_constraint("uq_newcomer_per_combo", type_="unique")
        batch.create_unique_constraint(
            "uq_newcomer_per_combo",
            ["country", "platform", "app_id", "chart_type"],
        )


def downgrade() -> None:
    with op.batch_alter_table("market_newcomer_log") as batch:
        batch.drop_constraint("uq_newcomer_per_combo", type_="unique")
        batch.create_unique_constraint(
            "uq_newcomer_per_combo",
            ["country", "platform", "app_id"],
        )
        batch.drop_column("chart_type")
