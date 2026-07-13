"""game_rankings: 复合索引 (country, platform, chart_type, date) 提速热读路径

Revision ID: 0042_gr_market_index
Revises: 0041_rss_chart_seen
Create Date: 2026-07-13

digest / movement / 月度 rollup 的热查询都按 (市场, 榜类型, 日期窗口) 过滤且**不带 app_id**，
用不上现有以 app_id 打头的唯一索引，SQLite 退回低选择性的单列 chart_type 索引（2 值 ≈ 半表
扫描，10 万+ 行时每次热读扫 ~一半）。EXPLAIN QUERY PLAN 实测：加本复合索引后从
「SEARCH USING ix_chart_type」变「SEARCH USING ix_game_rankings_market_date（前 3 列等值 +
date 范围精确 seek）」。纯新增索引，回滚走纯代码（旧码忽略多出来的索引）。
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0042_gr_market_index"
down_revision: Union[str, None] = "0041_rss_chart_seen"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_game_rankings_market_date",
        "game_rankings",
        ["country", "platform", "chart_type", "date"],
    )


def downgrade() -> None:
    op.drop_index("ix_game_rankings_market_date", table_name="game_rankings")
