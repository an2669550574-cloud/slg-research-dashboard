"""api quota daily counter (per-day breakdown alongside monthly)

Revision ID: 0005_quota_daily
Revises: 0004_material_uploads
Create Date: 2026-05-21

为仪表盘"配额历史曲线"提供每日粒度。旧表 api_quota_monthly 只有月总数,
看不出"近 7/30 天每日烧得快不快",6/1 池重置定预算时缺一手关键数据。
本迁移仅新建 daily 表;不回填历史(数据从未被记录到这种粒度,无源)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_quota_daily"
down_revision: Union[str, None] = "0004_material_uploads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_quota_daily",
        sa.Column("date", sa.String(10), primary_key=True),  # "YYYY-MM-DD" UTC
        sa.Column("count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("api_quota_daily")
