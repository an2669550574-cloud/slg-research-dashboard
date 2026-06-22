"""publisher_ignores：缺口忽略名单（把已知非 SLG 巨头从 /gaps 里剔掉）

Revision ID: 0023_publisher_ignores
Revises: 0022_newcomer_is_reentry
Create Date: 2026-06-22

缺口稳态被 ~17 个非 SLG 巨头（Niantic / Supercell / EA 等）刷屏 → banner-blind
（#84 因此整块下线缺口 UI）。本表存「人工标过不建档」的发行商 / app，让缺口收敛到
可操作信号，UI 才值得抬回。纯新增表，与 is_slg 判定无关、只影响缺口提示。回滚走纯
代码（旧代码忽略本表即可，无需 downgrade）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023_publisher_ignores"
down_revision: Union[str, None] = "0022_newcomer_is_reentry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "publisher_ignores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=20), nullable=False),  # 'publisher' | 'app_id'
        sa.Column("value", sa.String(length=200), nullable=False),  # corp_squash 键 / app_id
        sa.Column("label", sa.String(length=300), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("kind", "value", name="uq_publisher_ignore"),
    )
    op.create_index(
        "ix_publisher_ignores_value", "publisher_ignores", ["value"]
    )


def downgrade() -> None:
    op.drop_index("ix_publisher_ignores_value", table_name="publisher_ignores")
    op.drop_table("publisher_ignores")
