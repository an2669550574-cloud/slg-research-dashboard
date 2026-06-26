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
        # chart_type 进唯一约束：同一 app 在收入榜/下载榜各留一条检出（ADR 0001）。
        UniqueConstraint("country", "platform", "app_id", "chart_type",
                         name="uq_newcomer_per_combo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country: Mapped[str] = mapped_column(String(10))
    platform: Mapped[str] = mapped_column(String(10))
    app_id: Mapped[str] = mapped_column(String(200))
    # 检出来自哪个榜：grossing（收入榜，默认/存量）/ free（下载榜，切片 2 起）。
    chart_type: Mapped[str] = mapped_column(String(20), default="grossing",
                                            server_default="grossing")
    as_of: Mapped[str] = mapped_column(String(20))  # 检出锚定的快照日
    name: Mapped[str] = mapped_column(String(300))
    publisher: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    icon_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    revenue: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_slg: Mapped[bool] = mapped_column(Boolean, default=False)
    # 检出时是否「回归」（baseline 窗口之外曾出现）。新写入 True/False；
    # 0022 迁移前的历史行为 NULL（无法回溯当时 baseline，前端按真首发处理）。
    is_reentry: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
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
    # 当前版本号 + 版本更新日（iTunes lookup 同响应里有；GP 页拿不到，留 NULL）。
    version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    current_version_date: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    # 支持语言（ISO2A 逗号拼，封顶 30 个）。同上：iTunes 有、GP 留 NULL。
    languages: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    enrich_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    enriched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class NewcomerVideo(Base):
    """竞品新品的 YouTube 实机玩法视频候选（ADR 0002 切片 1b）。

    按游戏名搜来、挂 app_id（跨市场同名一致，故按 app_id 而非 combo 去重）。
    (app_id, video_id) 唯一防同一视频重复落。YT 独立配额，零 ST。
    """
    __tablename__ = "newcomer_video"
    __table_args__ = (
        UniqueConstraint("app_id", "video_id", name="uq_newcomer_video"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(200), index=True)
    video_id: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(500))
    channel: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    thumbnail: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    url: Mapped[str] = mapped_column(String(500))
    published_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 候选序，1 起
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class NewcomerVideoSearch(Base):
    """视频搜索台账：记「哪些 app 已搜过」。

    一行 = 对某 app_id 搜过一次（含搜出 0 条的情况——搜了就占配额）。三重作用：
    - 去重锚点：app_id 在表里 = 已搜，不再重复搜（省配额）。
    - 当日配额计数：searched_at 落在当天的行数 = 今日已用次数。
    - 「待搜」是隐式的 = market_newcomer_log 里 app_id 不在本表的（近 LOOKBACK 天）行，
      无需显式 pending 状态机：当日超额没搜的，下次 drain 自然仍在待搜集。
    """
    __tablename__ = "newcomer_video_search"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    searched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
