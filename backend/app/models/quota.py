from sqlalchemy import String, Integer, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from app.database import Base, utcnow_naive


class ApiQuotaMonthly(Base):
    __tablename__ = "api_quota_monthly"

    year_month: Mapped[str] = mapped_column(String(7), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)


class ApiQuotaDaily(Base):
    """每日粒度计数,与 ApiQuotaMonthly 同事务原子 +1。仅前向记录,
    上线前的历史不可恢复(从未被记到这种粒度)。"""
    __tablename__ = "api_quota_daily"

    date: Mapped[str] = mapped_column(String(10), primary_key=True)  # "YYYY-MM-DD" UTC
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)


class SensorTowerSnapshot(Base):
    __tablename__ = "sensor_tower_snapshots"

    cache_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
