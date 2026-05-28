from sqlalchemy import String, Integer, DateTime, Text, JSON, Float, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional
from app.database import Base, utcnow_naive

class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(300))
    # link 素材用 url；upload 素材用 file_*（url 置空）。两路二选一，故 url 可空。
    url: Mapped[str] = mapped_column(String(1000), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="link")  # link / upload
    file_path: Mapped[str] = mapped_column(String(500), nullable=True)  # MEDIA_ROOT 下相对路径
    file_name: Mapped[str] = mapped_column(String(300), nullable=True)  # 原始文件名（展示/下载）
    file_size: Mapped[int] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=True)  # youtube/tiktok/meta/other
    material_type: Mapped[str] = mapped_column(String(50), default="video")  # video/image/playable
    tags: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

    # ── LLM 视频分析（migration 0006）─────────────────────────────
    # pending(尚未分析) / running(后台任务跑中) / done / failed。None 视同 pending。
    analysis_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    analysis_brief: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # LLM 提议的 tags；独立于人工 tags，前端可"采纳到人工 tags"。
    analysis_tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    # 分镜：[{ts: 秒, description: 中文场景描述}]
    analysis_scenes: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    # 卸负点/转化钩子：[{ts: 秒, kind: 卸负/CTA/反转/..., note: 中文说明}]
    analysis_hooks: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    analyzed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    analysis_model: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    analysis_cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # 失败原因（用户决定是否重试）；不存堆栈，给中文/短英文摘要即可。
    analysis_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # 抽帧元信息（migration 0007）：[{"ts": float}, ...]，数组下标 = 帧文件名
    # frame_NN.jpg 的 N。具体路径走 services/video_analyze.frame_path 计算。
    analysis_frames: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    # 是否生成了联系单 jpg（5 列 × N 行的拼图，给前端抽屉顶部展示）
    analysis_has_contact_sheet: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
