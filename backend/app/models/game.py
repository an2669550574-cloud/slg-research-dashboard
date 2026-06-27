from sqlalchemy import String, Float, Integer, DateTime, Text, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.database import Base, utcnow_naive

# 榜类型：收入榜（grossing，历史唯一榜，存量行默认）与下载/免费榜（free，ADR 0001
# 起并行采集）。所有现有读路径默认只看 grossing；free 仅新品检测专用读。
CHART_GROSSING = "grossing"
CHART_FREE = "free"

class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    publisher: Mapped[str] = mapped_column(String(200), nullable=True)
    icon_url: Mapped[str] = mapped_column(String(500), nullable=True)
    category: Mapped[str] = mapped_column(String(100), default="SLG")
    platform: Mapped[str] = mapped_column(String(20), default="ios")
    country: Mapped[str] = mapped_column(String(10), default="US")
    release_date: Mapped[str] = mapped_column(String(20), nullable=True)
    # 当前 iOS 版本号 + 版本更新日：version_tracker 日级重查 iTunes 维护（零 ST）。
    # 变更历史进 game_histories(event_type='version')。Android 无版本源 → 留 NULL。
    version: Mapped[str] = mapped_column(String(50), nullable=True)
    version_date: Mapped[str] = mapped_column(String(20), nullable=True)
    # 精确 iOS 数字 trackId（人工核对补）：HK tracked games 多用 GP 包名作 app_id，
    # iTunes 用包名查不到 iOS 版本，靠这个补；version_tracker 优先用它走批量 lookup。
    ios_track_id: Mapped[str] = mapped_column(String(30), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

class GameRanking(Base):
    __tablename__ = "game_rankings"
    __table_args__ = (
        # 同 (app_id, date, country, platform, chart_type) 一条记录，防止 scheduler
        # 并发触发写重复；chart_type 进约束让收入榜/下载榜同 (市场,日) 并存不撞。
        UniqueConstraint("app_id", "date", "country", "platform", "chart_type",
                         name="uq_game_rankings_day_market"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(100), index=True)
    date: Mapped[str] = mapped_column(String(20), index=True)
    # 榜类型，默认 grossing（存量行 + 现有读路径口径）；free = 下载/免费榜（ADR 0001）。
    chart_type: Mapped[str] = mapped_column(String(20), default=CHART_GROSSING,
                                            server_default=CHART_GROSSING, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=True)
    downloads: Mapped[float] = mapped_column(Float, nullable=True)
    revenue: Mapped[float] = mapped_column(Float, nullable=True)
    country: Mapped[str] = mapped_column(String(10), default="US")
    platform: Mapped[str] = mapped_column(String(20), default="ios")
    # Sensor Tower 返回的元信息，存进来让 /games/rankings 不必每次跨表 join games
    # （并且 Top 8 里有未在 games 表追踪的应用，games 表也查不到名字）
    name: Mapped[str] = mapped_column(String(200), nullable=True)
    publisher: Mapped[str] = mapped_column(String(200), nullable=True)
    icon_url: Mapped[str] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class GameRegionRelease(Base):
    """tracked iOS 竞品分地区上架日（需求② 子项③ / ADR 0004）。

    每行 = 某 game 在某 storefront(country) 的 iOS releaseDate（iTunes lookup 同响应
    自带 releaseDate，零 ST）。(app_id, country) 唯一，按 app_id 聚合看「在哪些区先上、
    soft-launch 区序」。release_date 可空 = 该区查不到/未上架（resultCount=0 时也落一行
    记 NULL，与「该区另一个 trackId」区分不开，诚实留空）。Android 无可靠上架日源 →
    仅 platform='ios'（与版本追踪同取舍）。数据近静态，周级 job 刷新。
    """
    __tablename__ = "game_region_release"
    __table_args__ = (
        UniqueConstraint("app_id", "country", name="uq_game_region_release"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(100), index=True)
    country: Mapped[str] = mapped_column(String(10))
    release_date: Mapped[str] = mapped_column(String(20), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
