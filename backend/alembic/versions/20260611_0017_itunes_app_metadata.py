"""publisher_itunes_apps: 免费 iTunes 展示字段（icon/品类/评分/评价数/售价）

Revision ID: 0017_itunes_app_metadata
Revises: 0016_publisher_itunes_apps
Create Date: 2026-06-11

「新品只用免费源填充」：App Store 上架雷达每次 diff 已发起一次免费 iTunes lookup，
响应里本就含 icon/genre/rating/rating_count/price，之前全丢了。本迁移给
publisher_itunes_apps 补这 5 个可空列——零增量 ST 配额，纯展示。基线行不展示故无需
回填，只对将来真新上架生效。纯加列、向前兼容、回滚走纯代码。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_itunes_app_metadata"
down_revision: Union[str, None] = "0016_publisher_itunes_apps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("publisher_itunes_apps", sa.Column("artwork_url", sa.String(length=1000), nullable=True))
    op.add_column("publisher_itunes_apps", sa.Column("genre", sa.String(length=100), nullable=True))
    op.add_column("publisher_itunes_apps", sa.Column("rating", sa.Float(), nullable=True))
    op.add_column("publisher_itunes_apps", sa.Column("rating_count", sa.Integer(), nullable=True))
    op.add_column("publisher_itunes_apps", sa.Column("price", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("publisher_itunes_apps", "price")
    op.drop_column("publisher_itunes_apps", "rating_count")
    op.drop_column("publisher_itunes_apps", "rating")
    op.drop_column("publisher_itunes_apps", "genre")
    op.drop_column("publisher_itunes_apps", "artwork_url")
