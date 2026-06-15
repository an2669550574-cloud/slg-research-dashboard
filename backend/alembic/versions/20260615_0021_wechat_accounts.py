"""wechat_accounts：看板可维护的订阅公众号（取代硬编码 SUBSCRIBED_ACCOUNTS）

Revision ID: 0021_wechat_accounts
Revises: 0020_newcomer_log
Create Date: 2026-06-15

新品监测日报「附带行业文章」原先按硬编码的公众号列表搜文章；改为建表、看板增删/
启停，fakeid 由 wechat-api searchbiz 按名解析。纯新增表，起步数据在 backend 启动时
seed_wechat_accounts_if_empty 灌入（表空才灌，与 mock games / publishers 同款），不在
迁移里塞数据。回滚走纯代码。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0021_wechat_accounts"
down_revision: Union[str, None] = "0020_newcomer_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wechat_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("fakeid", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("fakeid", name="uq_wechat_account_fakeid"),
    )


def downgrade() -> None:
    op.drop_table("wechat_accounts")
