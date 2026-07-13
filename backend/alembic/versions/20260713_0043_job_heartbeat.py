"""job_heartbeat: 定时 job「上次成功完成」台账（补静默失败盲区）

Revision ID: 0043_job_heartbeat
Revises: 0042_gr_market_index
Create Date: 2026-07-13

scheduler 各 job 的 try/except 只 catch 崩溃（→ Sentry）；job 若停止被调度（禁用 / scheduler
没起来）既不崩也不产出，静默无人知（A3 前科）。本表 = 每个关键 job 成功完成后 upsert
last_ok_at；每日 digest 尾部自检超期 → 维护者卡 ⚠️。纯新增表，回滚走纯代码（旧码无此表）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0043_job_heartbeat"
down_revision: Union[str, None] = "0042_gr_market_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_heartbeat",
        sa.Column("job_name", sa.String(length=80), primary_key=True),
        sa.Column("last_ok_at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("job_heartbeat")
