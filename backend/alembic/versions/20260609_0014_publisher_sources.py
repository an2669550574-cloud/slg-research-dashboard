"""publisher_sources: 厂商主体调研出处（一手源溯源沉淀）

Revision ID: 0014_publisher_sources
Revises: 0013_publisher_entities
Create Date: 2026-06-09

给厂商主体加一张「调研出处」子表：每条来源带 url / 类型(一手/二手分级) / 可信度 /
核验日期 / 备注，把主体身份·归属·股权判断的依据沉淀下来、可回溯。纯新增表，
向前兼容、回滚走纯代码。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_publisher_sources"
down_revision: Union[str, None] = "0013_publisher_entities"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "publisher_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entity_id", sa.Integer(),
            sa.ForeignKey("publisher_entities.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=True),
        sa.Column("as_of", sa.String(length=20), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_publisher_sources_entity_id", "publisher_sources", ["entity_id"])


def downgrade() -> None:
    op.drop_index("ix_publisher_sources_entity_id", table_name="publisher_sources")
    op.drop_table("publisher_sources")
