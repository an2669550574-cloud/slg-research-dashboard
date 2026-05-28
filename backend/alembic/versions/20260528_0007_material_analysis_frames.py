"""materials: 加 analysis_frames + analysis_has_contact_sheet

Revision ID: 0007_material_analysis_frames
Revises: 0006_material_llm_analysis
Create Date: 2026-05-28

LLM 视频分析升级：抽出的关键帧持久化到磁盘 + 拼成联系单 JPG，给前端
抽屉做"顶部概览 + 每条分镜配缩略图"。

DB 只记 ts 数组和"是否有联系单"两个字段；具体文件路径走 deterministic
（services/video_analyze.frame_path / contact_sheet_path），不进 DB，
迁移和重构时也不需要管。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_material_analysis_frames"
down_revision: Union[str, None] = "0006_material_llm_analysis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("materials") as batch:
        batch.add_column(sa.Column("analysis_frames", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("analysis_has_contact_sheet", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("materials") as batch:
        batch.drop_column("analysis_has_contact_sheet")
        batch.drop_column("analysis_frames")
