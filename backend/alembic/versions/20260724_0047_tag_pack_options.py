"""tag_pack_options：标签包成员细化到二级标签（选项子集）

包成员两种形态并存（0046 的 tag_pack_dimensions 语义不变）：
- 整维度（tag_pack_dimensions）＝包含该维度全部二级标签，新增选项自动进包
- 选项子集（本表）＝只含勾中的选项，固定名单
同一包内某维度两种形态互斥，API 写入时归一（整维度优先）。
纯加表、可回滚 = drop 本表。

Revision ID: 0047_tag_pack_options
Revises: 0046_tag_packs
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0047_tag_pack_options"
down_revision: Union[str, None] = "0046_tag_packs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tag_pack_options",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pack_id", sa.Integer(),
                  sa.ForeignKey("tag_packs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("option_id", sa.Integer(),
                  sa.ForeignKey("tag_options.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tag_pack_options_pack_id", "tag_pack_options", ["pack_id"])
    op.create_index("ix_tag_pack_options_option_id", "tag_pack_options", ["option_id"])


def downgrade() -> None:
    op.drop_index("ix_tag_pack_options_option_id", table_name="tag_pack_options")
    op.drop_index("ix_tag_pack_options_pack_id", table_name="tag_pack_options")
    op.drop_table("tag_pack_options")
