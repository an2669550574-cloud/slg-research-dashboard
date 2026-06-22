"""market_newcomer_log.is_reentry：检出当时是真首发还是回归

Revision ID: 0022_newcomer_is_reentry
Revises: 0021_wechat_accounts
Create Date: 2026-06-21

PR #93 在检测层引入 is_reentry（baseline 窗口之外曾出现 = 老游戏回归而非真首发）但只
透传给 digest 过滤；本迁移让其在检出沉淀里也固化，让前端「新品监测」历史卡片也能
区分真首发 vs 回归（默认筛仅真首发，回归独立 tab 给运营回看）。

字段可空：迁移前已落库的历史行无法准确回溯当时是否回归（baseline 已变），保持 NULL
表示「未知」。前端把 NULL 当作「真首发」处理（向后兼容，老数据照旧显示）。新写入
始终带 True/False。回滚走纯代码 drop column。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0022_newcomer_is_reentry"
down_revision: Union[str, None] = "0021_wechat_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("market_newcomer_log") as batch:
        batch.add_column(sa.Column("is_reentry", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("market_newcomer_log") as batch:
        batch.drop_column("is_reentry")
