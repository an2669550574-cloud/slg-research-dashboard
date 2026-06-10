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
from app.services.slg_publishers import is_slg, _tokens

logger = logging.getLogger(__name__)


async def _first_appearances(
    country: str,
    platform: str,
    window: int,
) -> dict:
    """as_of 当期相对过去 window 个快照的「首次出现」行（**不限名次**）。

    detect_newcomers（全市场新面孔，Top N 门槛）与 detect_publisher_newcomers
    （已建档厂商新品，任意名次）共享这套基线比对核心。
    """
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
        # 首次出现的 GameRanking 行（已按名次升序、不限名次）。
        "rows": [],
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

    summary["rows"] = [r for r in today_rows if r.app_id not in baseline_ids]
    return summary


def _row_dict(r) -> dict:
    return {
        "app_id": r.app_id,
        "name": r.name or r.app_id,
        "publisher": r.publisher,
        "icon_url": r.icon_url,
        "rank": r.rank,
        "revenue": r.revenue,
        "downloads": r.downloads,
    }


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

    base = await _first_appearances(country, platform, window)
    summary = {k: v for k, v in base.items() if k != "rows"}
    summary["newcomers"] = [
        {
            **_row_dict(r),
            # 不参与过滤，仅供前端区分"已识别 SLG"vs"新厂商待识别"(后者最值得调研)。
            "is_slg": is_slg(r.app_id, r.publisher),
        }
        for r in base["rows"]
        if r.rank is not None and r.rank <= topn
    ]
    return summary


def _kw_hit(pub_tokens: list[str], kw_tokens: tuple[str, ...]) -> bool:
    """kw_tokens 作为连续子序列出现在 pub_tokens 里即命中（与 is_slg_publisher 同规则）。"""
    n = len(kw_tokens)
    if n == 0:
        return False
    for i in range(len(pub_tokens) - n + 1):
        if tuple(pub_tokens[i:i + n]) == kw_tokens:
            return True
    return False


async def _load_entity_matchers() -> list[dict]:
    """已建档主体的归属匹配器：alias keyword token 串 + 钉选 app_id 集合。

    直查 DB 而非 slg_publishers 内存索引——索引只回答布尔 is_slg，这里要把产品
    归属到**具体主体**（entity_id/name）。量级几十主体，每次现查开销可忽略。
    """
    from app.models.publisher import PublisherEntity, PublisherAlias, PublisherAppId

    async with AsyncSessionLocal() as db:
        entities = (await db.execute(select(PublisherEntity))).scalars().all()
        aliases = (await db.execute(select(PublisherAlias))).scalars().all()
        app_ids = (await db.execute(select(PublisherAppId))).scalars().all()

    kw_by_entity: dict[int, list[tuple[str, ...]]] = {}
    for a in aliases:
        t = tuple(_tokens(a.keyword))
        if t:
            kw_by_entity.setdefault(a.entity_id, []).append(t)
    ids_by_entity: dict[int, set[str]] = {}
    for a in app_ids:
        ids_by_entity.setdefault(a.entity_id, set()).add(a.app_id)

    return [
        {
            "entity_id": e.id,
            "entity_name": e.name,
            "kw_tokens": kw_by_entity.get(e.id, []),
            "app_ids": ids_by_entity.get(e.id, set()),
        }
        for e in entities
        if kw_by_entity.get(e.id) or ids_by_entity.get(e.id)
    ]


async def detect_publisher_newcomers(
    country: str,
    platform: str,
    *,
    window: Optional[int] = None,
    matchers: Optional[list[dict]] = None,
) -> dict:
    """已建档厂商主体的新品：首次出现 + 发行商马甲/钉选 app_id 归属到某主体。

    与 detect_newcomers 的差异：**不设 Top N 门槛**——主体可信，新品在任意名次
    首次出现都是高信号（解决"慢慢爬榜被基线见过、永不触发"的漏报，如 Top Heroes）。
    跨 combo 调用时可传入预加载的 matchers 避免重复查主体表。
    """
    window = window if window is not None else settings.NEWCOMER_WINDOW
    if matchers is None:
        matchers = await _load_entity_matchers()

    base = await _first_appearances(country, platform, window)
    summary = {k: v for k, v in base.items() if k != "rows"}
    summary["newcomers"] = []
    for r in base["rows"]:
        pub_tokens = _tokens(r.publisher)
        for m in matchers:
            if r.app_id in m["app_ids"]:
                matched = "app_id"
            elif pub_tokens and any(_kw_hit(pub_tokens, kw) for kw in m["kw_tokens"]):
                matched = "alias"
            else:
                continue
            summary["newcomers"].append({
                **_row_dict(r),
                "entity_id": m["entity_id"],
                "entity_name": m["entity_name"],
                "matched_by": matched,
            })
            break  # 一个产品归属到第一个命中的主体即可
    return summary
