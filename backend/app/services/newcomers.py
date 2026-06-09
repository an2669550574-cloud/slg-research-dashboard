"""新品监测：本地零配额「新面孔」检测。

「新面孔」(newcomer) = 某 app_id 在过去 W 个同步快照里**从没出现过**、却在最近一次
同步(as_of)进入 Top N 的产品。纯读已落库的 game_rankings，零 ST 配额、零新基建。

与 movement(竞品异动) 的区别——**互补**关系：
- movement: 今日 vs 昨日 TopN 的进/退/收入异动，且**只看 is_slg 白名单**，抓"老熟人的进退"。
- newcomers: 跨过去 W 个快照的"首次出现"，且**故意不走 is_slg 过滤**——全新产品的
  发行商往往还没进 SLG 白名单(白名单滞后维护)，过滤会把最该看的新厂商新品筛掉。
  对全策略榜开口，再给每行打 `is_slg` 标记供前端区分"已识别 SLG / 新厂商待识别"。

锚点取**最近一次已同步的快照日**(as_of，不强求等于今天)——同步降到周级后多数天
没有"今日"行，锚最近快照才能让页面始终有内容(与 /games/rankings 读路径一致)。

设计取舍：
- baseline = as_of **之前** W 个不同快照日里出现过的全部 app_id(全榜，不限 TopN)。
  用"全榜历史"而非"TopN 历史"判定"见过"，避免把长期在 30–50 名徘徊、本期升进
  TopN 的老产品误报为新面孔。
- 只有 0 个历史快照(冷库/首次同步)时 no_baseline=True、返回空——无从判断"新"，
  绝不把首图全员当新品。
- 只看 as_of 当期 rank ≤ TopN 的行：榜尾噪声大，且新品要"够亮"才值得提示。
- 这是按**本地榜单存在性**判定，是真实上线日的零配额代理(proxy)，不等于产品发布日。
"""
import logging
from typing import Optional

from sqlalchemy import select, func, distinct

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.game import GameRanking
from app.services.slg_publishers import is_slg

logger = logging.getLogger(__name__)


async def detect_newcomers(
    country: str,
    platform: str,
    *,
    window: Optional[int] = None,
    topn: Optional[int] = None,
) -> dict:
    """**纯检测**——比对 as_of 当期榜与之前 W 个快照，返回结构化"新面孔"摘要。
    无任何副作用、零 ST 配额，可被 API endpoint 任意频次调用。
    """
    window = window if window is not None else settings.NEWCOMER_WINDOW
    topn = topn if topn is not None else settings.NEWCOMER_TOPN
    today = utcnow_naive().strftime("%Y-%m-%d")

    summary: dict = {
        "country": country,
        "platform": platform,
        # 锚定的"最近快照日"。None = 该 combo 库内完全无数据。
        "as_of": None,
        # 用作基线的历史快照日(升序)。
        "baseline_dates": [],
        # 是否缺历史快照(冷库/首次同步)，无从判断"新面孔"。
        "no_baseline": False,
        "newcomers": [],
    }

    async with AsyncSessionLocal() as db:
        # 最近一次已同步快照日(<= today)。同步周级化后多数天无"今日"行，锚最近快照。
        as_of = (await db.execute(
            select(func.max(GameRanking.date)).where(
                GameRanking.country == country,
                GameRanking.platform == platform,
                GameRanking.date <= today,
            )
        )).scalar()
        if not as_of:
            return summary
        summary["as_of"] = as_of

        # as_of 之前最近 W 个不同快照日。
        prior_dates = (await db.execute(
            select(distinct(GameRanking.date)).where(
                GameRanking.country == country,
                GameRanking.platform == platform,
                GameRanking.date < as_of,
            ).order_by(GameRanking.date.desc()).limit(window)
        )).scalars().all()
        if not prior_dates:
            summary["no_baseline"] = True
            return summary
        summary["baseline_dates"] = sorted(prior_dates)

        # baseline：W 个历史快照里出现过的全部 app_id(全榜，不限名次)。
        baseline_ids = set((await db.execute(
            select(distinct(GameRanking.app_id)).where(
                GameRanking.country == country,
                GameRanking.platform == platform,
                GameRanking.date.in_(prior_dates),
            )
        )).scalars().all())

        # as_of 当期榜，按名次升序。
        today_rows = (await db.execute(
            select(GameRanking).where(
                GameRanking.country == country,
                GameRanking.platform == platform,
                GameRanking.date == as_of,
            ).order_by(GameRanking.rank.asc().nulls_last())
        )).scalars().all()

    for r in today_rows:
        if r.rank is None or r.rank > topn:
            continue
        if r.app_id in baseline_ids:
            continue
        summary["newcomers"].append({
            "app_id": r.app_id,
            "name": r.name or r.app_id,
            "publisher": r.publisher,
            "icon_url": r.icon_url,
            "rank": r.rank,
            "revenue": r.revenue,
            "downloads": r.downloads,
            # 不参与过滤，仅供前端区分"已识别 SLG"vs"新厂商待识别"(后者最值得调研)。
            "is_slg": is_slg(r.app_id, r.publisher),
        })
    return summary
