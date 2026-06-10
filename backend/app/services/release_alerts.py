"""新品监测 → 钉钉推送：把三层监测的"有事发生"主动送到群里，不用人盯页面。

触达时机（与检测口径一一对应，不引入新口径）：
- 榜单两层（全市场 TopN 空降 + 厂商×任意名次）：每个 combo **定时同步成功后**
  检测一次并推送非空摘要——新快照落库的那次正好是新面孔的"首报"窗口，天然去重。
  手动 refresh 不经此路径，不会刷屏。
- App Store 清单层：周级 sync 里 diff 出 new_apps>0 时推送一条汇总。

所有发送走 services/dingtalk（未配 webhook = 静默 no-op；失败不抛、不拖垮同步）。
digest 构建是纯函数，单测直接断言 markdown 文本。
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.publisher import PublisherEntity, PublisherItunesApp, PublisherItunesArtist
from app.services import dingtalk

logger = logging.getLogger(__name__)


def _fmt_money(v) -> str:
    return f"${v:,.0f}" if v else "—"


def build_chart_digest(market: dict, publisher: dict) -> Optional[tuple[str, str]]:
    """两层榜单检测摘要 → (title, markdown)。两层都空 → None（不发）。"""
    m_items = market.get("newcomers") or []
    p_items = publisher.get("newcomers") or []
    if not m_items and not p_items:
        return None
    combo = f"{market['country']}/{market['platform']}"
    as_of = market.get("as_of") or ""
    lines = [f"### 🆕 SLG 新品监测 · {combo}（快照 {as_of}）"]
    if m_items:
        lines.append(f"**全市场新面孔 · 空降 Top 榜（{len(m_items)}）**")
        for n in m_items[:10]:
            slg = "" if n.get("is_slg") else " ⚠️新厂商待识别"
            lines.append(f"- #{n['rank']} {n['name']} — {n.get('publisher') or '?'}"
                         f"（{_fmt_money(n.get('revenue'))}）{slg}")
        if len(m_items) > 10:
            lines.append(f"- …等共 {len(m_items)} 款，看板查看全部")
    if p_items:
        lines.append(f"**厂商新品 · 已建档主体首次进榜（{len(p_items)}）**")
        for n in p_items[:10]:
            lines.append(f"- {n['entity_name']}：{n['name']} #{n['rank'] or '—'}")
        if len(p_items) > 10:
            lines.append(f"- …等共 {len(p_items)} 款")
    return f"新品监测 {combo}", "\n\n".join(lines)


async def alert_chart_newcomers(country: str, platform: str) -> bool:
    """combo 同步成功后调用：检测两层榜单新品并推送（非空才发）。"""
    if not dingtalk.is_enabled():
        return False
    from app.services.newcomers import detect_newcomers, detect_publisher_newcomers
    market = await detect_newcomers(country, platform)
    publisher = await detect_publisher_newcomers(country, platform)
    msg = build_chart_digest(market, publisher)
    if msg is None:
        return False
    return await dingtalk.send_markdown(*msg)


def build_appstore_digest(rows: list[tuple]) -> Optional[tuple[str, str]]:
    """rows: [(PublisherItunesApp, entity_name, artist_label)] → (title, markdown)。"""
    if not rows:
        return None
    lines = [f"### 📱 SLG · App Store 新上架（开发者清单 diff，{len(rows)} 款）"]
    for app, entity_name, _label in rows[:15]:
        released = f"（上架 {app.release_date}）" if app.release_date else ""
        link = f" [App Store]({app.track_view_url})" if app.track_view_url else ""
        lines.append(f"- {entity_name}：{app.name}{released}{link}")
    if len(rows) > 15:
        lines.append(f"- …等共 {len(rows)} 款")
    lines.append("> 未进榜也能抓到；详见看板「新品监测 → 厂商新品」")
    return "App Store 新上架", "\n\n".join(lines)


async def alert_appstore_releases(since: datetime) -> bool:
    """iTunes 清单同步后调用：推送本轮（first_seen_at >= since）的非基线新上架。"""
    if not dingtalk.is_enabled():
        return False
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(PublisherItunesApp, PublisherEntity.name, PublisherItunesArtist.label)
            .join(PublisherEntity, PublisherEntity.id == PublisherItunesApp.entity_id)
            .join(PublisherItunesArtist, PublisherItunesArtist.id == PublisherItunesApp.artist_row_id)
            .where(
                PublisherItunesApp.is_baseline.is_(False),
                PublisherItunesApp.first_seen_at >= since,
            )
            .order_by(PublisherItunesApp.id)
        )).all()
    msg = build_appstore_digest(list(rows))
    if msg is None:
        return False
    return await dingtalk.send_markdown(*msg)
