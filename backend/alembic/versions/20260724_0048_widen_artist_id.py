"""widen publisher_itunes_artists.artist_id 30→255（容纳名称型 GP 开发者账号 id）

GP 开发者账号 id 有两种形态：dev?id= 的数字型、developer?id= 的名称型
（如 "SINGAPORE JUST GAME TECHNOLOGY PTE. LTD."，40 字符）。原 String(30) 把长
名称型 GP dev id 挡在雷达接入外（schema 校验 len>30 报 422 + 列限双拦），导致
「雷达覆盖建议」里这类候选点「接入雷达」必然失败。加宽到 255 后可正常接入。
注：SQLite 不强制 VARCHAR 长度，本迁移对现有 SQLite 数据零影响（batch recreate
保留 unique 约束与数据），主要为 model/DB/schema 声明一致 + 未来迁库安全。
可回滚 = 收窄回 30（前提是已无 >30 的 artist_id）。

Revision ID: 0048_widen_artist_id
Revises: 0047_tag_pack_options
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0048_widen_artist_id"
down_revision: Union[str, None] = "0047_tag_pack_options"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("publisher_itunes_artists") as batch:
        batch.alter_column(
            "artist_id",
            existing_type=sa.String(30),
            type_=sa.String(255),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("publisher_itunes_artists") as batch:
        batch.alter_column(
            "artist_id",
            existing_type=sa.String(255),
            type_=sa.String(30),
            existing_nullable=False,
        )
