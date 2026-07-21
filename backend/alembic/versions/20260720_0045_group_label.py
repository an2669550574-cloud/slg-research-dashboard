"""add group_label to publisher_entities

资本集团报表口径（2026-07-20）：集团**成员名单**由 publisher_relations 推导
（services/publisher_groups：structural 边并组、minority 不并组），不落库；本列只存
**组名**——推导出的根主体名常常不是报表想要的叫法（根叫「元趣娱乐」，报表要「元趣系」）。

打在组内任一成员上都生效（推导时根优先、否则取组内 id 最小的有标签者），实践中打在根上。
NULL = 用回退名（根主体名）。纯加列、可空 → 回滚走纯代码，旧码忽略本列。

Revision ID: 0045_group_label
Revises: 0044_name_cn
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0045_group_label"
down_revision: Union[str, None] = "0044_name_cn"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("publisher_entities",
                  sa.Column("group_label", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("publisher_entities", "group_label")
