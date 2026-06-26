"""market_newcomer_log: add version / current_version_date / languages

Revision ID: 0028_newcomer_log_detail_fields
Revises: 0027_newcomer_log_chart_type
Create Date: 2026-06-25

切片 3.1：新品详情面板补全。iTunes lookup 同响应里本就有版本号 / 版本更新日 /
支持语言，当初富化只挑了一部分落库，这里把它们也存下来供详情抽屉展示。
- 三列均纯新增可空列，无 server_default、存量行留 NULL（下次富化时回填）。
- 旧代码忽略这几列即可，回滚走纯代码（无需 downgrade）；downgrade 仍提供。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0028_newcomer_log_detail_fields"
down_revision: Union[str, None] = "0027_newcomer_log_chart_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("market_newcomer_log") as batch:
        batch.add_column(sa.Column("version", sa.String(50), nullable=True))
        batch.add_column(sa.Column("current_version_date", sa.String(30), nullable=True))
        batch.add_column(sa.Column("languages", sa.String(300), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("market_newcomer_log") as batch:
        batch.drop_column("languages")
        batch.drop_column("current_version_date")
        batch.drop_column("version")
