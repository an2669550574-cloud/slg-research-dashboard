"""game_region_release: tracked iOS 竞品分地区上架日（需求② 子项③ / ADR 0004）

Revision ID: 0033_game_region_release
Revises: 0032_newcomer_video_hidden_at
Create Date: 2026-06-27

需求② 子项③「分地区上线时间对照」：新表存 tracked iOS 竞品在各 storefront 的
releaseDate（iTunes lookup 同响应自带，零 ST）。(app_id, country) 唯一，周级 job
刷新。纯新增表，回滚走纯代码（旧码无此表无副作用），downgrade 仍提供。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0033_game_region_release"
down_revision: Union[str, None] = "0032_newcomer_video_hidden_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "game_region_release",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_id", sa.String(length=100), nullable=False),
        sa.Column("country", sa.String(length=10), nullable=False),
        sa.Column("release_date", sa.String(length=20), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("app_id", "country", name="uq_game_region_release"),
    )
    op.create_index("ix_game_region_release_app_id", "game_region_release", ["app_id"])


def downgrade() -> None:
    op.drop_index("ix_game_region_release_app_id", table_name="game_region_release")
    op.drop_table("game_region_release")
