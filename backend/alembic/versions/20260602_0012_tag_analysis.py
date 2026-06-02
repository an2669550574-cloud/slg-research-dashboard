"""tag_analysis: AI 标签分析对话两表（会话 / 消息）

Revision ID: 0012_tag_analysis
Revises: 0011_tag_taxonomy
Create Date: 2026-06-02

「AI 导出标签分析」：对当前筛选范围的素材标签 + 已有 AI 分析内容做对话式分析。
tag_analysis_sessions（范围快照 + 模型）+ tag_analysis_messages（user/assistant 轮）。
纯新增表，向前兼容、回滚走纯代码（旧代码不引用即无视）。走公司 LLM 网关，零 ST 配额。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_tag_analysis"
down_revision: Union[str, None] = "0011_tag_taxonomy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tag_analysis_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("app_id", sa.String(length=100), nullable=True),
        sa.Column("material_type", sa.String(length=50), nullable=True),
        sa.Column("tag_options", sa.String(length=500), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "tag_analysis_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id", sa.Integer(),
            sa.ForeignKey("tag_analysis_sessions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("material_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_tag_analysis_messages_session_id", "tag_analysis_messages", ["session_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_tag_analysis_messages_session_id", table_name="tag_analysis_messages")
    op.drop_table("tag_analysis_messages")
    op.drop_table("tag_analysis_sessions")
