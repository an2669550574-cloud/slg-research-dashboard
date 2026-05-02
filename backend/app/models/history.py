from sqlalchemy import String, Integer, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.database import Base

class GameHistory(Base):
    __tablename__ = "game_histories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(100), index=True)
    event_date: Mapped[str] = mapped_column(String(20))
    event_type: Mapped[str] = mapped_column(String(50))  # launch/version/ranking/revenue/marketing
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="manual")  # manual/ai/appstore/sensortower
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
