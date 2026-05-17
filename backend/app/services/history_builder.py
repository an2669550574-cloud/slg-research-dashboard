"""数据驱动的发展历程：只用**事实**，全中文，不靠 LLM 编造。

来源（零 ST 配额、无 Anthropic）：
- iTunes 元信息：优先 tw 区（繁中本地化）取上线日 + 当前版本号 + 官方
  更新说明；tw 查不到回退 us（仅取日期，绝不往界面贴英文营销文案）。
  仅 iOS 数字 app_id 命中；Android 包名查不到（iTunes 没安卓）。
- 本地 game_rankings（每日调度累积）：首次纳入监测、上榜阈值突破、
  自监测起最高排名、单日收入峰值、多市场覆盖。文案只说「自监测起」，
  不谎称「史上」；阈值/峰值类随天数累积自动变厚。

真营销事件（超级碗广告 / KOL 投放）无任何数据源 → 只能「手动添加」。
"""
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.game import GameRanking
from app.services.appstore import fetch_app_info

logger = logging.getLogger(__name__)

# 阈值由严到松；debut 即达标的不算「突破」（debut 事件已表述）
_RANK_TIERS = ((1, "登顶畅销榜 #1"), (3, "进入畅销榜 Top 3"), (10, "进入畅销榜 Top 10"))


def _market(r: GameRanking) -> str:
    return f"{r.country}/{r.platform}"


async def build_history(app_id: str, db: AsyncSession) -> list[dict]:
    """拼出该 app 的事实性中文时间线，按日期排序。无事实可取时返回空列表。"""
    events: list[dict] = []

    # iTunes：优先繁中（tw），查不到回退 us。zh_text=False 时不取其英文文案。
    info = await fetch_app_info(app_id, country="tw")
    zh_text = info is not None
    if info is None:
        info = await fetch_app_info(app_id, country="us")

    if info:
        name = info.get("name") or app_id
        pub = info.get("publisher") or "未知发行商"
        if info.get("release_date"):
            events.append({
                "event_date": info["release_date"],
                "event_type": "launch",
                "title": "App Store 全球上线",
                "description": f"{name}（{pub}）在 App Store 上线。",
            })
        if info.get("version") and info.get("current_version_date"):
            notes = (info.get("release_notes") or "").strip() if zh_text else ""
            events.append({
                "event_date": info["current_version_date"],
                "event_type": "version",
                "title": f"更新至 v{info['version']}",
                "description": notes[:500] or "App Store 当前版本（暂无中文更新说明）。",
            })

    all_rows = (await db.execute(
        select(GameRanking).where(GameRanking.app_id == app_id).order_by(GameRanking.date)
    )).scalars().all()
    # 排名类里程碑只看有 rank 的行（scheduler 前向采集）；收入/下载峰值看全部
    # 行——含历史回填的 rank=NULL 行，让一年纵深也能进时间线。
    ranked = [r for r in all_rows if r.rank is not None]

    if ranked:
        first = ranked[0]
        span = f"监测区间 {ranked[0].date} 至 {ranked[-1].date}"
        events.append({
            "event_date": first.date,
            "event_type": "ranking",
            "title": f"首次纳入监测，{_market(first)} #{first.rank}",
            "description": f"开始持续追踪该产品在策略畅销榜的表现（{span}）。",
        })
        for tier, label in _RANK_TIERS:
            if first.rank <= tier:
                continue  # 首测即达标，不是「突破」
            crossed = next((r for r in ranked if r.date > first.date and r.rank <= tier), None)
            if crossed:
                events.append({
                    "event_date": crossed.date,
                    "event_type": "ranking",
                    "title": f"首次{label}",
                    "description": f"{_market(crossed)} 策略畅销榜，当日 #{crossed.rank}。",
                })
        best = min(ranked, key=lambda r: r.rank)
        if best.rank < first.rank:  # 仅当 debut 之后确有爬升，避免与首测重复
            events.append({
                "event_date": best.date,
                "event_type": "ranking",
                "title": f"自监测起最高排名 #{best.rank}",
                "description": f"{_market(best)} 策略畅销榜（{span}）。",
            })
        markets = sorted({_market(r) for r in ranked})
        if len(markets) >= 2:
            events.append({
                "event_date": ranked[0].date,
                "event_type": "ranking",
                "title": f"覆盖 {len(markets)} 个市场策略榜",
                "description": "、".join(markets) + "。",
            })

    rev = [r for r in all_rows if r.revenue]
    if rev:
        top = max(rev, key=lambda r: r.revenue)
        span_all = f"区间 {all_rows[0].date} 至 {all_rows[-1].date}"
        rk = f"，当日排名 #{top.rank}" if top.rank is not None else ""
        dl = f"，下载 {top.downloads:,.0f}" if top.downloads else ""
        events.append({
            "event_date": top.date,
            "event_type": "revenue",
            "title": f"单日收入峰值 ${top.revenue:,.0f}",
            "description": f"{_market(top)}{rk}{dl}（{span_all}）。",
        })

    events.sort(key=lambda e: e["event_date"])
    return events
