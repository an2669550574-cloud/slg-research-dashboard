"""publisher_itunes_apps: 多区可见性 + 检出详情（描述/截图/语言）

Revision ID: 0018_itunes_app_detail
Revises: 0017_itunes_app_metadata
Create Date: 2026-06-11

「一上线就知道」升级：清单 diff 从单一美区扩到软启动区（PH/CA/AU/SG），
storefronts 记录该 app 在哪些区可见——「PH/CA 可见、美区不可见」= 软启动中，
本身就是关键情报。description/screenshot_urls/languages 同样出自那一次免费
iTunes lookup 响应，零增量 ST 配额。纯加列、向前兼容、回滚走纯代码。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_itunes_app_detail"
down_revision: Union[str, None] = "0017_itunes_app_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("publisher_itunes_apps", sa.Column("storefronts", sa.String(length=200), nullable=True))
    op.add_column("publisher_itunes_apps", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("publisher_itunes_apps", sa.Column("screenshot_urls", sa.Text(), nullable=True))
    op.add_column("publisher_itunes_apps", sa.Column("languages", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("publisher_itunes_apps", "languages")
    op.drop_column("publisher_itunes_apps", "screenshot_urls")
    op.drop_column("publisher_itunes_apps", "description")
    op.drop_column("publisher_itunes_apps", "storefronts")
