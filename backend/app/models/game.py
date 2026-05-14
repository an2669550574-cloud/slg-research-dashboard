from sqlalchemy import String, Float, Integer, DateTime, Text, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.database import Base, utcnow_naive

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
    description: Mapped[str] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)

class GameRanking(Base):
    __tablename__ = "game_rankings"
    __table_args__ = (
        # 同 (app_id, date, country, platform) 一条记录，防止 scheduler 并发触发写重复
        UniqueConstraint("app_id", "date", "country", "platform", name="uq_game_rankings_day_market"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(100), index=True)
    date: Mapped[str] = mapped_column(String(20), index=True)
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
