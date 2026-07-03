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


class WechatArticleSent(Base):
    """行业动态段（平淡日兜底广搜）已推文章台账：跨天去重。

    行业段是泛关键词广搜（`WECHAT_INDUSTRY_KEYWORDS`，非挂新品），此前只靠
    `WECHAT_INDUSTRY_DAYS` 时间窗控跨天重复——连续平淡日会把同一篇文章重复推给领导群。
    发送成功后按 link 落一行，后续广搜结果里已在台账的 link 全过滤掉，保证每天见到没推过的。
    link 唯一（去重键，天然兜并发/重复插入）；first_sent_date 供 prune（超 retention 天的
    老行删掉，防表无限膨胀）。纯新增表，回滚走纯代码（旧码无此表、只是回退到时窗去重）。
    """
    __tablename__ = "wechat_article_sent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    link: Mapped[str] = mapped_column(String(500), unique=True, index=True)  # 文章链接 = 去重键
    title: Mapped[str] = mapped_column(String(300), nullable=True)           # 便于人读排查
    first_sent_date: Mapped[str] = mapped_column(String(20), index=True)     # YYYY-MM-DD (UTC)，prune 用
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
