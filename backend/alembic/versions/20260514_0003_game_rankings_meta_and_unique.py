"""game_rankings: add name/publisher/icon_url + unique(app_id,date,country,platform)

Revision ID: 0003_rankings_meta
Revises: 0002_quota_snapshots
Create Date: 2026-05-14

将 Sensor Tower 返回的元信息一起持久化，让 /api/games/rankings 可以直接读
game_rankings 表而不再每次打 Sensor Tower。同时给 (app_id, date, country,
platform) 加联合 unique，幂等同步写入。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_rankings_meta"
down_revision: Union[str, None] = "0002_quota_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite 在 batch 模式下会复制旧表 → 加新列 / 约束 → 改名替换，等价于"重建"
    with op.batch_alter_table("game_rankings") as batch:
        batch.add_column(sa.Column("name", sa.String(200), nullable=True))
        batch.add_column(sa.Column("publisher", sa.String(200), nullable=True))
        batch.add_column(sa.Column("icon_url", sa.String(500), nullable=True))
        batch.create_unique_constraint(
            "uq_game_rankings_day_market",
            ["app_id", "date", "country", "platform"],
        )


def downgrade() -> None:
    with op.batch_alter_table("game_rankings") as batch:
        batch.drop_constraint("uq_game_rankings_day_market", type_="unique")
        batch.drop_column("icon_url")
        batch.drop_column("publisher")
        batch.drop_column("name")
