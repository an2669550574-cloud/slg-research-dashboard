"""games: add ios_track_id (精确 iOS trackId，版本追踪用)

Revision ID: 0031_games_ios_track_id
Revises: 0030_games_version_fields
Create Date: 2026-06-27

需求②版本追踪（ADR 0003）：HK tracked games 多用 GP 包名作 app_id，iTunes 用包名
查不到 iOS 版本。加 ios_track_id 存人工核对的精确 iOS 数字 trackId，version_tracker
优先用它走批量 lookup。纯新增可空列，存量行 NULL（没补 trackId 的 app 跳过、不追踪），
回滚走纯代码（无需 downgrade）；downgrade 仍提供。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0031_games_ios_track_id"
down_revision: Union[str, None] = "0030_games_version_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("games") as batch:
        batch.add_column(sa.Column("ios_track_id", sa.String(30), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("games") as batch:
        batch.drop_column("ios_track_id")
