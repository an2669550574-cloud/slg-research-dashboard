"""add name_cn to market_newcomer_log and app_subgenre

游戏名中译（领导反馈「非中文元素太多看着累」）：日文假名 / 西文原名领导读不懂。
两张表各加一列，与既有中文化字段同源产出（同一次 LLM 调用，零增量成本）：
- market_newcomer_log.name_cn：新品，随 summary_cn / subgenre_cn 一起产出
- app_subgenre.name_cn：存量竞品（movement 老熟人），随子品类回补一起产出

两列均可空。NULL = 未译（尚未 drain 到 / LLM 没给出）；渲染层无译名时回落原名，
故上线后立刻可用、不需要等回补跑完。

Revision ID: 0044_name_cn
Revises: 0043_job_heartbeat
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044_name_cn"
down_revision: Union[str, None] = "0043_job_heartbeat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("market_newcomer_log",
                  sa.Column("name_cn", sa.String(length=200), nullable=True))
    op.add_column("app_subgenre",
                  sa.Column("name_cn", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("app_subgenre", "name_cn")
    op.drop_column("market_newcomer_log", "name_cn")
