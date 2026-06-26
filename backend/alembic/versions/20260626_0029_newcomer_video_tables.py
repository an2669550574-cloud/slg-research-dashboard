"""newcomer_video + newcomer_video_search: 新品实机玩法视频搜集

Revision ID: 0029_newcomer_video_tables
Revises: 0028_newcomer_log_detail_fields
Create Date: 2026-06-26

ADR 0002 切片 1b：竞品新品自动搜集 YouTube 实机玩法视频的持久层。
- newcomer_video：视频候选（按 app_id 挂，(app_id, video_id) 唯一防重）。
- newcomer_video_search：搜索台账（去重锚点 + 当日配额计数；待搜是隐式的）。
两张纯新增表，回滚直接 drop（无数据迁移、不动既有表），故 downgrade 安全。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0029_newcomer_video_tables"
down_revision: Union[str, None] = "0028_newcomer_log_detail_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "newcomer_video",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_id", sa.String(200), nullable=False),
        sa.Column("video_id", sa.String(40), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("channel", sa.String(300), nullable=True),
        sa.Column("thumbnail", sa.String(1000), nullable=True),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("published_at", sa.String(30), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        # NOT NULL 对齐 model（ORM 端 default=utcnow_naive 总填值）。
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("app_id", "video_id", name="uq_newcomer_video"),
    )
    op.create_index("ix_newcomer_video_app_id", "newcomer_video", ["app_id"])
    op.create_index("ix_newcomer_video_created_at", "newcomer_video", ["created_at"])

    op.create_table(
        "newcomer_video_search",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_id", sa.String(200), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        # NOT NULL 对齐 model（ORM 端 default=0 / utcnow_naive 总填值）。
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("searched_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("app_id", name="uq_newcomer_video_search_app"),
    )
    op.create_index("ix_newcomer_video_search_searched_at",
                    "newcomer_video_search", ["searched_at"])


def downgrade() -> None:
    op.drop_index("ix_newcomer_video_search_searched_at", "newcomer_video_search")
    op.drop_table("newcomer_video_search")
    op.drop_index("ix_newcomer_video_created_at", "newcomer_video")
    op.drop_index("ix_newcomer_video_app_id", "newcomer_video")
    op.drop_table("newcomer_video")
