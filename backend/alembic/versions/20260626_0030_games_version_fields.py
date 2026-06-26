"""games: add version / version_date (iOS 版本变更追踪)

Revision ID: 0030_games_version_fields
Revises: 0029_newcomer_video_tables
Create Date: 2026-06-26

需求②版本追踪（ADR 0003）：给 tracked games 存当前 iOS 版本号 + 版本更新日，
作为 version_tracker 日级重查 iTunes 的比对基准。两列纯新增可空，存量行 NULL
（首次重查时填基线、不算变更），回滚走纯代码（无需 downgrade）；downgrade 仍提供。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0030_games_version_fields"
down_revision: Union[str, None] = "0029_newcomer_video_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("games") as batch:
        batch.add_column(sa.Column("version", sa.String(50), nullable=True))
        batch.add_column(sa.Column("version_date", sa.String(20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("games") as batch:
        batch.drop_column("version_date")
        batch.drop_column("version")
