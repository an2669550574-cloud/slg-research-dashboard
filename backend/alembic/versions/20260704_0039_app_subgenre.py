"""app_subgenre: 存量竞品玩法子品类分类（同赛道匹配补齐）

Revision ID: 0039_app_subgenre
Revises: 0038_wechat_article_sent
Create Date: 2026-07-04

market_newcomer_log.subgenre_cn 只在新品翻译时产出，覆盖不到 established 竞品（movement
老熟人）+ subgenre 特性上线前的老检出行。本表按 app_id 全局补分类，digest 建 own_matches
时作 fallback → ⚔️ 同赛道对老竞品也生效。subgenre_cn 可空 = 已尝试但 LLM 未给词表内值
（写行即「已尝试」不再重试）。纯新增表，回滚走纯代码（旧码无此表、own_matches 只少 fallback
源、退回原行为），downgrade 仍提供。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0039_app_subgenre"
down_revision: Union[str, None] = "0038_wechat_article_sent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_subgenre",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_id", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("subgenre_cn", sa.String(length=40), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column("classified_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("app_id", name="uq_app_subgenre_app_id"),
    )
    op.create_index("ix_app_subgenre_app_id", "app_subgenre", ["app_id"])


def downgrade() -> None:
    op.drop_index("ix_app_subgenre_app_id", table_name="app_subgenre")
    op.drop_table("app_subgenre")
