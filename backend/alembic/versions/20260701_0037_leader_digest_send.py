"""leader_digest_send: 领导群每日 digest 幂等标记（一天最多推一次）

Revision ID: 0037_leader_digest_send
Revises: 0036_subgenre_match
Create Date: 2026-07-01

领导群每天最多推一次：容器在 03:00–04:00 UTC 之间重启会让 daily_alert_digest
misfire 补跑（misfire_grace_time=3600，故意保留以防真漏发），从而**重复**推领导群。
发送成功后按 send_date（UTC）落一行，下轮命中即跳过。仅领导群（维护者群重发无碍）。
纯新增表，回滚走纯代码（旧码无此表无副作用），downgrade 仍提供。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0037_leader_digest_send"
down_revision: Union[str, None] = "0036_subgenre_match"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "leader_digest_send",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("send_date", sa.String(length=20), nullable=False),
        sa.Column("content_hash", sa.String(length=32), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("send_date", name="uq_leader_digest_send_date"),
    )
    op.create_index("ix_leader_digest_send_date", "leader_digest_send", ["send_date"])


def downgrade() -> None:
    op.drop_index("ix_leader_digest_send_date", table_name="leader_digest_send")
    op.drop_table("leader_digest_send")
