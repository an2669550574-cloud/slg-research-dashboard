"""market_newcomer_log: add summary_cn / description_cn（新品中文化）

Revision ID: 0034_newcomer_log_cn_fields
Revises: 0033_game_region_release
Create Date: 2026-06-27

新品监测可读性优化：商店描述是源区语言（日/韩/英），团队读中文费劲。LLM 网关给
is_slg 新品生成 summary_cn（一句话「这是什么游戏」，进 digest + 抽屉副标题）+
description_cn（描述全文中译，抽屉可切原文）。纯新增可空列，回滚走纯代码（旧码忽略
新列），downgrade 仍提供。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0034_newcomer_log_cn_fields"
down_revision: Union[str, None] = "0033_game_region_release"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("market_newcomer_log") as batch:
        batch.add_column(sa.Column("summary_cn", sa.String(200), nullable=True))
        batch.add_column(sa.Column("description_cn", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("market_newcomer_log") as batch:
        batch.drop_column("description_cn")
        batch.drop_column("summary_cn")
