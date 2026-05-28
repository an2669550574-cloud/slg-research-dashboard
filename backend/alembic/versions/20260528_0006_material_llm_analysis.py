"""materials: 加 LLM 视频分析字段 (brief / tags / scenes / hooks / 成本)

Revision ID: 0006_material_llm_analysis
Revises: 0005_quota_daily
Create Date: 2026-05-28

素材库新增 AI 视觉分析能力：用户点"分析"按钮 → ffmpeg 抽关键帧 → 太石网关
sonnet/opus 模型 → 解析回 brief/tags/scenes/hooks。本迁移在 materials 表
原地加列，不另起表：分析一对一，列表页需要按状态/标签筛选，独立表会让
绝大多数查询都要 join 而无收益（见会话讨论决议）。

新增字段：
- analysis_status:    pending / running / done / failed
- analysis_brief:     一段中文总结
- analysis_tags:      LLM 提议的标签数组（独立于人工 tags，前端可"采纳"促入 tags）
- analysis_scenes:    分镜数组 [{ts, description}]
- analysis_hooks:     卸负点/转化钩子数组 [{ts, kind, note}]
- analyzed_at:        最近一次分析完成时间
- analysis_model:     用的模型 ID（如 claude-sonnet-4.5）
- analysis_cost_usd:  本次分析估算成本（美元）
- analysis_error:     失败时的简短原因（前端展示给用户决定是否重试）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_material_llm_analysis"
down_revision: Union[str, None] = "0005_quota_daily"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("materials") as batch:
        batch.add_column(sa.Column("analysis_status", sa.String(20), nullable=True))
        batch.add_column(sa.Column("analysis_brief", sa.Text(), nullable=True))
        batch.add_column(sa.Column("analysis_tags", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("analysis_scenes", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("analysis_hooks", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("analyzed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("analysis_model", sa.String(80), nullable=True))
        batch.add_column(sa.Column("analysis_cost_usd", sa.Float(), nullable=True))
        batch.add_column(sa.Column("analysis_error", sa.String(500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("materials") as batch:
        batch.drop_column("analysis_error")
        batch.drop_column("analysis_cost_usd")
        batch.drop_column("analysis_model")
        batch.drop_column("analyzed_at")
        batch.drop_column("analysis_hooks")
        batch.drop_column("analysis_scenes")
        batch.drop_column("analysis_tags")
        batch.drop_column("analysis_brief")
        batch.drop_column("analysis_status")
