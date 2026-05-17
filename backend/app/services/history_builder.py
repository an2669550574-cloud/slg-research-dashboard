"""数据驱动的发展历程：只用**事实**，不靠 LLM 编造。

来源（零 ST 配额、无 Anthropic）：
- iTunes 元信息（已接入的免费查询）：原始上线日、当前版本号 + 官方更新说明。
  仅 iOS 数字 app_id 命中；Android 包名查不到（iTunes 没安卓）。
- 本地 game_rankings（每日调度累积）：自监测起的最高排名、单日收入峰值。
  随天数累积越来越厚，文案只说「自监测起」，不谎称「史上」。

真营销事件（超级碗广告 / KOL 投放）无任何数据源 → 只能「手动添加」。
"""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.game import GameRanking
from app.services.appstore import fetch_app_info

logger = logging.getLogger(__name__)


async def build_history(app_id: str, db: AsyncSession) -> list[dict]:
    """拼出该 app 的事实性时间线，按日期排序返回。无事实可取时返回空列表。"""
    events: list[dict] = []

    info = await fetch_app_info(app_id)
    if info:
        if info.get("release_date"):
            events.append({
                "event_date": info["release_date"],
                "event_type": "launch",
                "title": "App Store 全球上线",
                "description": (info.get("description") or "")[:300],
            })
        if info.get("version") and info.get("current_version_date"):
            notes = (info.get("release_notes") or "").strip()
            events.append({
                "event_date": info["current_version_date"],
                "event_type": "version",
                "title": f"更新至 v{info['version']}",
                "description": notes[:500] or "App Store 当前版本（无更新说明）",
            })

    rows = (await db.execute(
        select(GameRanking).where(
            GameRanking.app_id == app_id,
            GameRanking.rank.isnot(None),
        ).order_by(GameRanking.date)
    )).scalars().all()
    if rows:
        best = min(rows, key=lambda r: r.rank)
        events.append({
            "event_date": best.date,
            "event_type": "ranking",
            "title": f"自监测起最高排名 #{best.rank}",
            "description": f"{best.country}/{best.platform} 策略畅销榜，"
                           f"监测区间 {rows[0].date} 至 {rows[-1].date}。",
        })
        rev = [r for r in rows if r.revenue]
        if rev:
            top = max(rev, key=lambda r: r.revenue)
            events.append({
                "event_date": top.date,
                "event_type": "revenue",
                "title": f"自监测起单日收入峰值 ${top.revenue:,.0f}",
                "description": f"{top.country}/{top.platform}，当日排名 #{top.rank}。",
            })

    events.sort(key=lambda e: e["event_date"])
    return events
