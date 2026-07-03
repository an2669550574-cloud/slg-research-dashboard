"""wechat_article_sent: 行业动态段已推文章台账（跨天去重）

Revision ID: 0038_wechat_article_sent
Revises: 0037_leader_digest_send
Create Date: 2026-07-03

平淡日「SLG 行业动态」段是泛关键词广搜，此前只靠 WECHAT_INDUSTRY_DAYS 时间窗控跨天
重复，连续平淡日会把同一篇文章重复推领导群。发送成功后按 link 落一行，后续广搜结果里
已在台账的 link 全过滤掉。link 唯一（去重键）；first_sent_date 供 prune。
纯新增表，回滚走纯代码（旧码无此表、只是回退到时窗去重），downgrade 仍提供。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0038_wechat_article_sent"
down_revision: Union[str, None] = "0037_leader_digest_send"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wechat_article_sent",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("link", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("first_sent_date", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("link", name="uq_wechat_article_sent_link"),
    )
    op.create_index("ix_wechat_article_sent_link", "wechat_article_sent", ["link"])
    op.create_index("ix_wechat_article_sent_first_date", "wechat_article_sent", ["first_sent_date"])


def downgrade() -> None:
    op.drop_index("ix_wechat_article_sent_first_date", table_name="wechat_article_sent")
    op.drop_index("ix_wechat_article_sent_link", table_name="wechat_article_sent")
    op.drop_table("wechat_article_sent")
