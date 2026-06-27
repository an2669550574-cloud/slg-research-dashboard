"""own_products: add match_keywords（对标我方哪款）

Revision ID: 0035_own_product_match_keywords
Revises: 0034_newcomer_log_cn_fields
Create Date: 2026-06-28

决策锚点：digest 现只有竞品 name/rank/revenue，不告诉领导「这竞品对标我方哪款」。
给我方产品（own_products）加一列逗号分隔的题材关键词（如「丧尸,末日,survival,zombie」），
digest 用它对竞品名 + LLM 中文摘要做纯本地小写子串匹配，命中就给该行打「⚔️ 对标《X》」。
纯新增可空列、零 ST/零 LLM，回滚走纯代码（旧码忽略新列），downgrade 仍提供。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0035_own_product_match_keywords"
down_revision: Union[str, None] = "0034_newcomer_log_cn_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("own_products") as batch:
        batch.add_column(sa.Column("match_keywords", sa.String(500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("own_products") as batch:
        batch.drop_column("match_keywords")
