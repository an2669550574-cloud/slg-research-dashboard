"""publisher_relations: 主体间股权/母子关系（母公司 → 子公司 + 持股%）

Revision ID: 0015_publisher_relations
Revises: 0014_publisher_sources
Create Date: 2026-06-09

给厂商主体加一张自关联表：有向边 parent_id(母公司/投资方) → child_id(子公司/被投)，
带 relation_type(全资/控股/参股/关联) + 选填 stake_pct(持股%)。(parent_id, child_id)
唯一。纯新增表，向前兼容、回滚走纯代码。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_publisher_relations"
down_revision: Union[str, None] = "0014_publisher_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "publisher_relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "parent_id", sa.Integer(),
            sa.ForeignKey("publisher_entities.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "child_id", sa.Integer(),
            sa.ForeignKey("publisher_entities.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("relation_type", sa.String(length=30), nullable=False),
        sa.Column("stake_pct", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("parent_id", "child_id", name="uq_publisher_relation_pair"),
    )
    op.create_index("ix_publisher_relations_parent_id", "publisher_relations", ["parent_id"])
    op.create_index("ix_publisher_relations_child_id", "publisher_relations", ["child_id"])


def downgrade() -> None:
    op.drop_index("ix_publisher_relations_child_id", table_name="publisher_relations")
    op.drop_index("ix_publisher_relations_parent_id", table_name="publisher_relations")
    op.drop_table("publisher_relations")
