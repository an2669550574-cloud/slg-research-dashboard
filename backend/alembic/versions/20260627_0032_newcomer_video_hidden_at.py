"""newcomer_video: add hidden_at (软删去噪，保留噪声样本)

Revision ID: 0032_newcomer_video_hidden_at
Revises: 0031_games_ios_track_id
Create Date: 2026-06-27

ADR 0002（新品视频搜集）观察缺口：人工去噪原是硬删（物理 delete），噪声样本被
彻底丢弃 → 无法回溯统计召回噪声率、也拿不到设计停用词所需的「真实最糟样本」。
改软删：delete 端点置 hidden_at 而非物删；列表默认过滤 hidden。纯新增可空列，
存量行 NULL（= 未隐藏），回滚走纯代码（旧代码忽略本列，行为等价于「都不隐藏」），
downgrade 仍提供。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0032_newcomer_video_hidden_at"
down_revision: Union[str, None] = "0031_games_ios_track_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("newcomer_video") as batch:
        batch.add_column(sa.Column("hidden_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("newcomer_video") as batch:
        batch.drop_column("hidden_at")
