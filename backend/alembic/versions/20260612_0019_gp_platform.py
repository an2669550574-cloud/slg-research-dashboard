"""publisher_itunes_artists: platform 列（ios / gp）——雷达扩到 Google Play 侧

Revision ID: 0019_gp_platform
Revises: 0018_itunes_app_detail
Create Date: 2026-06-12

GP 盲区补口：SLG 常 GP 先软启动（如 GAME SPARK 的 Top King 只在 GP），iOS-only
雷达结构性看不见。复用同一套清单 diff/基线语义，platform 区分账号侧：
- 'ios' = iTunes artistId（免费 lookup API）
- 'gp'  = Google Play 开发者页 id（免费公开页面，名称型或数字型）
GP 行的 storefronts 固定为 'gp'。纯加列、server_default 'ios' 存量零影响、
回滚走纯代码。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0019_gp_platform"
down_revision: Union[str, None] = "0018_itunes_app_detail"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "publisher_itunes_artists",
        sa.Column("platform", sa.String(length=10), nullable=False, server_default="ios"),
    )


def downgrade() -> None:
    op.drop_column("publisher_itunes_artists", "platform")
