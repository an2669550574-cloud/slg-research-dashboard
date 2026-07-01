from sqlalchemy import String, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from app.database import Base, utcnow_naive


class LeaderDigestSend(Base):
    """领导群每日 digest 幂等标记：一天最多推一次。

    daily_alert_digest 的 misfire 补跑（容器在 03:00–04:00 UTC 之间重启会触发，见
    scheduler misfire_grace_time=3600——故意保留以防真漏发当日必达 digest）会导致领导群
    **重复**收卡。发送成功后按 send_date（UTC）落一行，下轮命中即跳过。仅领导群
    （维护者群是运维向，重发无碍、不设限）。send_date 唯一，天然兜并发/竞态。
    """
    __tablename__ = "leader_digest_send"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    send_date: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # YYYY-MM-DD (UTC)
    content_hash: Mapped[str] = mapped_column(String(32), nullable=True)  # 内容指纹，仅供排查
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
