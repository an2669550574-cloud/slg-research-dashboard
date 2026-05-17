"""竞品异动检测：每日同步后比对 game_rankings「今日 vs 上一可用日」。

零配额、零新基建——纯读已落库的 game_rankings；SLG 行里有显著变化就
**汇总成一条** logger.error，经现有 LoggingIntegration 推送 Sentry。

设计取舍：
- 一次 (country, platform) 只发一条事件（多条异动合并进同一条消息），
  沿用配额告警那套「合并单事件、不刷屏」的纪律。
- 无可比对的历史日（冷库 / 首次同步）→ 静默跳过，绝不发空告警。
- 只看 TopN 内：榜尾对竞品监控无意义，且收入仅 Top20 有值。
- 只比对 is_slg 行：用户要的是 SLG 竞品动向，不是策略榜全量噪声。
- 仅定时任务路径调用（见 scheduler._scheduled_sync）；手动 refresh /
  trigger 走裸 sync，不触发，避免每次刷新都告警。
"""
import logging
from sqlalchemy import select
from app.config import settings
from app.database import AsyncSessionLocal
from app.models.game import GameRanking
from app.services.slg_publishers import is_slg

logger = logging.getLogger(__name__)


async def detect_and_alert_movement(country: str, platform: str, today: str) -> dict:
    """比对今日与上一可用日，检测 SLG 竞品异动并按需告警。返回结构化摘要（供测试）。"""
    summary = {
        "country": country, "platform": platform, "today": today,
        "prev_date": None, "new_entrants": [], "surges": [],
        "drops": [], "revenue_spikes": [],
    }
    if not settings.COMPETITOR_ALERT_ENABLED:
        return summary

    topn = settings.COMPETITOR_ALERT_TOPN
    jump = settings.COMPETITOR_RANK_JUMP
    rev_pct = settings.COMPETITOR_REVENUE_PCT

    async with AsyncSessionLocal() as db:
        # 上一可用日：不一定是昨天（scheduler 可能漏过几天），取 < today 的最近一天。
        prev_date = (await db.execute(
            select(GameRanking.date).where(
                GameRanking.country == country,
                GameRanking.platform == platform,
                GameRanking.date < today,
            ).order_by(GameRanking.date.desc()).limit(1)
        )).scalar_one_or_none()
        if not prev_date:
            return summary
        summary["prev_date"] = prev_date

        async def _rows(date):
            res = await db.execute(select(GameRanking).where(
                GameRanking.country == country,
                GameRanking.platform == platform,
                GameRanking.date == date,
            ))
            return res.scalars().all()

        today_rows = await _rows(today)
        prev_rows = await _rows(prev_date)

    prev = {r.app_id: r for r in prev_rows}
    cur = {r.app_id: r for r in today_rows}

    def _label(r):
        return r.name or r.app_id

    # 今日 TopN 内的 SLG：新进 / 窜升 / 收入异动
    for r in today_rows:
        if r.rank is None or r.rank > topn or not is_slg(r.app_id, r.publisher):
            continue
        p = prev.get(r.app_id)
        if p is None or p.rank is None or p.rank > topn:
            summary["new_entrants"].append((_label(r), p.rank if p else None, r.rank))
            continue
        if p.rank - r.rank >= jump:
            summary["surges"].append((_label(r), p.rank, r.rank))
        if p.revenue and r.revenue is not None and p.revenue > 0:
            pct = (r.revenue - p.revenue) / p.revenue * 100
            if abs(pct) >= rev_pct:
                summary["revenue_spikes"].append((_label(r), p.revenue, r.revenue, pct))

    # 上一日在 TopN 的 SLG：今日跌出 TopN / 彻底掉榜
    for p in prev_rows:
        if p.rank is None or p.rank > topn or not is_slg(p.app_id, p.publisher):
            continue
        c = cur.get(p.app_id)
        if c is None or c.rank is None or c.rank > topn:
            summary["drops"].append((_label(p), p.rank, c.rank if c else None))

    _emit(summary)
    return summary


def _emit(s: dict) -> None:
    parts = []
    for name, prev_r, cur_r in s["new_entrants"]:
        frm = "榜外" if prev_r is None else f"#{prev_r}"
        parts.append(f"[NEW] {name} 新进Top榜 ({frm}->#{cur_r})")
    for name, prev_r, cur_r in s["surges"]:
        parts.append(f"[UP] {name} #{prev_r}->#{cur_r} (升{prev_r - cur_r})")
    for name, prev_r, cur_r in s["drops"]:
        to = "榜外" if cur_r is None else f"#{cur_r}"
        parts.append(f"[DOWN] {name} 跌出Top榜 (#{prev_r}->{to})")
    for name, pv, cv, pct in s["revenue_spikes"]:
        parts.append(f"[REV] {name} 收入{pct:+.0f}% (${pv:,.0f}->${cv:,.0f})")
    if not parts:
        logger.info(
            "Competitor movement %s/%s %s vs %s: no significant SLG movement",
            s["country"], s["platform"], s["today"], s["prev_date"],
        )
        return
    logger.error(
        "[COMPETITOR-MOVEMENT] %s/%s %s vs %s — %d 项SLG异动:\n%s",
        s["country"], s["platform"], s["today"], s["prev_date"], len(parts),
        "\n".join("  - " + p for p in parts),
    )
