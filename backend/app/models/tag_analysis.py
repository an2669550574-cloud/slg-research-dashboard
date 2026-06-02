"""AI 标签分析对话（P6，migration 0012）。

「AI 导出标签分析」：在素材页对当前筛选范围（material_type + 分面筛选）的素材
标签 + 已有 AI 分析内容做对话式分析。一次「分析」= 一个 session（含范围快照 +
模型），下面挂多条 message（user 提问 / assistant 回答）。落库可回查、可导出
（md/csv）。走公司统一 LLM 网关（relay.tuyoo.com，OpenAI 兼容），消耗 LLM 额度
不碰 Sensor Tower 配额。范围快照只存查询参数，每轮按参数实时重算素材集——保证
分析跟随当下库存，而非冻结在建会话那一刻。
"""
from sqlalchemy import String, Integer, DateTime, Text, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional
from app.database import Base, utcnow_naive


class TagAnalysisSession(Base):
    """一次标签分析会话。范围快照 = 建会话时的素材筛选参数（每轮按此重算 scope）。"""
    __tablename__ = "tag_analysis_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))  # 会话标题（首次报告自动取/范围摘要）
    # 范围快照（与素材列表 /api/materials 同口径）：留空 = 全量
    app_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    material_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # 分面筛选：逗号分隔的二级标签 id 串（与 tag_options 查询参数一致）
    tag_options: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    model: Mapped[str] = mapped_column(String(80))  # 该会话用的网关模型
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class TagAnalysisMessage(Base):
    """会话中的一条消息。只持久化 user/assistant 两类对话轮；system 提示词与范围
    数据块每轮实时重建、不入库（避免冻结过期数据、也省存储）。"""
    __tablename__ = "tag_analysis_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tag_analysis_sessions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(20))  # user / assistant
    content: Mapped[str] = mapped_column(Text)  # markdown 文本
    # 以下仅 assistant 行有值（计费追踪）
    model: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 该轮分析覆盖的素材数（快照，供回查时显示「分析了 N 条」）
    material_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
