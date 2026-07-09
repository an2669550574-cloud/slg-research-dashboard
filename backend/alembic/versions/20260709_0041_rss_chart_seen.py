"""rss_chart_seen: RSS 早鸟信号层「已见」台账（ADR 0005）

Revision ID: 0041_rss_chart_seen
Revises: 0040_is_slg_align
Create Date: 2026-07-09

次市场（JP/KR）ST 快照双周一拍，新品检出平均滞后 ~7 天。Apple 旧版分类维度 RSS
（topgrossingapplications genre=7017）经 2026-07-09 探针验证仍在服务且日更——用它做
零 ST 的日级早鸟信号。本表 = 每国已见 app 台账（首轮整榜作基线不报，之后 diff 出
新面孔）。纯新增表，回滚走纯代码（旧码不读此表）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0041_rss_chart_seen"
down_revision: Union[str, None] = "0040_is_slg_align"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rss_chart_seen",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("country", sa.String(length=10), nullable=False),
        sa.Column("app_id", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("publisher", sa.String(length=300), nullable=True),
        sa.Column("first_seen_date", sa.String(length=20), nullable=False),
        sa.Column("first_rank", sa.Integer(), nullable=True),
        sa.Column("last_seen_date", sa.String(length=20), nullable=False),
        sa.Column("last_rank", sa.Integer(), nullable=True),
        sa.Column("is_baseline", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("country", "app_id", name="uq_rss_seen_country_app"),
    )
    op.create_index("ix_rss_chart_seen_country", "rss_chart_seen", ["country"])
    op.create_index("ix_rss_chart_seen_app_id", "rss_chart_seen", ["app_id"])


def downgrade() -> None:
    op.drop_index("ix_rss_chart_seen_app_id", table_name="rss_chart_seen")
    op.drop_index("ix_rss_chart_seen_country", table_name="rss_chart_seen")
    op.drop_table("rss_chart_seen")
