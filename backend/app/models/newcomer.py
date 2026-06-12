"""全市场新面孔检出日志（新品监测 v2）。

定时同步检出即落库——每 (country, platform, app_id) 唯一，首报一次永不重报；
页面回看 30/90 天历史而不是只有"本期"。落库时用免费源富化（iOS=iTunes lookup /
Android=GP 页 JSON-LD，零 ST 配额），失败留 NULL 不丢检出信号。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, utcnow_naive


class MarketNewcomerLog(Base):
    __tablename__ = "market_newcomer_log"
    __table_args__ = (
        UniqueConstraint("country", "platform", "app_id", name="uq_newcomer_per_combo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country: Mapped[str] = mapped_column(String(10))
    platform: Mapped[str] = mapped_column(String(10))
    app_id: Mapped[str] = mapped_column(String(200))
    as_of: Mapped[str] = mapped_column(String(20))  # 检出锚定的快照日
    name: Mapped[str] = mapped_column(String(300))
    publisher: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    icon_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    revenue: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_slg: Mapped[bool] = mapped_column(Boolean, default=False)
    first_detected_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, index=True)

    # ── 免费源富化（失败整组留 NULL，enrich_source 标记来源 itunes/gp）──
    store_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    release_date: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    genre: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rating_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    screenshot_urls: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list
    enrich_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    enriched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
