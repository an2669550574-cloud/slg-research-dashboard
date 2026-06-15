from sqlalchemy import String, Integer, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from app.database import Base, utcnow_naive


class WechatAccount(Base):
    """订阅的行业公众号：新品监测日报按这些号搜相关文章。

    fakeid 由 wechat-api 的 /api/public/searchbiz 按名解析得到，看板维护（增删/启停），
    取代原先硬编码在 services/wechat_articles 的 SUBSCRIBED_ACCOUNTS。enabled=False 的号
    保留记录但不参与搜索。fakeid 全局唯一。
    """
    __tablename__ = "wechat_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))  # 公众号昵称
    fakeid: Mapped[str] = mapped_column(String(100), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
