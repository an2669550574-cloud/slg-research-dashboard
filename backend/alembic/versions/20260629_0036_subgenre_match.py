"""玩法子品类：market_newcomer_log.subgenre_cn + own_products.match_subgenre

Revision ID: 0036_subgenre_match
Revises: 0035_own_product_match_keywords
Create Date: 2026-06-29

「对标我方哪款」原靠题材关键词（丧尸/末日…）子串匹配，先天太宽泛——「末日」横跨城建/
塔防/益智/基地建设/数字门各品类，分不出「数字门玩法 SLG」（无尽火线真赛道）vs「基地建设
SLG」。根因：匹配的文本（名+摘要）没有玩法机制维度。

修法：LLM 中文化时多分类一个「玩法子品类」（受控词表：数字门SLG/基地建设SLG/塔防/…，按
核心机制非题材判），存 market_newcomer_log.subgenre_cn；own_products 加 match_subgenre
（我方产品的目标子品类，如无尽火线=数字门SLG），digest 按子品类**相等**精确匹配。

两列均纯新增可空、零 ST。subgenre_cn 由 LLM 同一次调用产出（零额外调用）。回滚走纯代码
（旧码忽略新列），downgrade 仍提供。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0036_subgenre_match"
down_revision: Union[str, None] = "0035_own_product_match_keywords"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("market_newcomer_log") as batch:
        batch.add_column(sa.Column("subgenre_cn", sa.String(40), nullable=True))
    with op.batch_alter_table("own_products") as batch:
        batch.add_column(sa.Column("match_subgenre", sa.String(100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("own_products") as batch:
        batch.drop_column("match_subgenre")
    with op.batch_alter_table("market_newcomer_log") as batch:
        batch.drop_column("subgenre_cn")
