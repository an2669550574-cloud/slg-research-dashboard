"""market_newcomer_log.is_slg 存量对齐（app_id 级 OR 传播的一次性回补）

Revision ID: 0040_is_slg_align
Revises: 0039_app_subgenre
Create Date: 2026-07-08

is_slg 是检出时按该 combo 本地化 publisher 串冻结在行上的，次市场商店返回韩/日/俄文
厂商名导致同一 app_id 跨 combo 判定分裂（prod 实锤：Last Furry KR-ios=1 但 JP/KR 其余
combo=0；Evony/Gunship Battle 等明显 SLG 整体标 0 属白名单 miss，本迁移治不了、由非拉丁
alias 匹配路径 + 建档治）。数据迁移：任一行 is_slg=1 或 app_id 已是 tracked game 的，
该 app_id 全部行置 1。方向单边（只升不降），配套代码层已加前进式对齐（record 路径）防
新分裂。downgrade 无法还原原始分裂状态（信息已合并），置 no-op——回退代码不受影响：
旧码读该列只会多见几行 is_slg=1（且这些行本就该是 1）。
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0040_is_slg_align"
down_revision: Union[str, None] = "0039_app_subgenre"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE market_newcomer_log SET is_slg = 1
        WHERE is_slg = 0 AND app_id IN (
            SELECT DISTINCT app_id FROM market_newcomer_log WHERE is_slg = 1
            UNION
            SELECT app_id FROM games
        )
        """
    )


def downgrade() -> None:
    # 数据迁移不可逆（原始分裂状态已合并），且合并方向本就是正确值——no-op。
    pass
