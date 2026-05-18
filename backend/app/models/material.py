from sqlalchemy import String, Integer, DateTime, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
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
