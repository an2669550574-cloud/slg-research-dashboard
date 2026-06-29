"""新品监测：本地零配额「新面孔」检测。

「新面孔」(newcomer) = 某 app_id 在过去 W 个同步快照里**从没出现过**、却在最近一次
同步(as_of)进入 Top N 的产品。纯读已落库的 game_rankings，零 ST 配额、零新基建。

与 movement(竞品异动) 的区别——**互补**关系：
- movement: 今日 vs 昨日 TopN 的进/退/收入异动，且**只看 is_slg 白名单**，抓"老熟人的进退"。
- newcomers: 跨过去 W 个快照的"首次出现"，且**故意不走 is_slg 过滤**——全新产品的
  发行商往往还没进 SLG 白名单(白名单滞后维护)，过滤会把最该看的新厂商新品筛掉。
  对全策略榜开口，再给每行打 `is_slg` 标记供前端区分"已识别 SLG / 新厂商待识别"。
  **唯一例外**：人工逐条确认的非 SLG 发行商 / 单品(`publisher_ignores`，与 /gaps
  同一名单)会被剔除——这是"确认噪声"(误挂 strategy 标签的麻将/扑克/塔防/宝可梦对战
  等)，与"保住未识别的真新厂"初衷不冲突：不在名单里的新厂(如新出海 SLG)仍照常浮现。

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
import asyncio
import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy import select, func, distinct

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.game import GameRanking, CHART_GROSSING
from app.services.slg_publishers import is_slg, _tokens
from app.services.name_match import corp_squash

logger = logging.getLogger(__name__)


async def _first_appearances(
    country: str,
    platform: str,
    window: int,
    chart_type: str = CHART_GROSSING,
) -> dict:
    """as_of 当期相对过去 window 个快照的「首次出现」行（**不限名次**）。

    chart_type 默认 grossing（收入榜，现有口径）；切片 2 起可传 free 在下载榜上
    独立比对——baseline 也按同一 chart_type 取，两榜互不串。

    detect_newcomers（全市场新面孔，Top N 门槛）与 detect_publisher_newcomers
    （已建档厂商新品，任意名次）共享这套基线比对核心。

    同时返回 `historical_ids` —— baseline 窗口**之外**（更早的快照里）曾出现过的
    app_id 集合，用于让消费方区分「真首发」(从未见过) vs「回归」(老游戏短暂跌出
    baseline 又回来)。weekly combo (JP/KR/DE/RU) baseline = 4 周，老 SLG 产品有
    一周漏榜就会被基线判 "首次出现"，但 historical_ids 能告诉你它其实早就上过榜。
    digest 等高信噪需求的消费方可据此过滤；前端"新品监测"可保留两类、加 tag 展示。
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
        # baseline 窗口之外更早出现过的 app_id 集合（用于 is_reentry 判定）。
        # 仅当存在 baseline 时才计算；no_baseline 路径恒为空集。
        "historical_ids": set(),
    }

    async with AsyncSessionLocal() as db:
        # 最近一次已同步快照日(<= today)。同步周级化后多数天无"今日"行，锚最近快照。
        as_of = (await db.execute(
            select(func.max(GameRanking.date)).where(
                GameRanking.country == country,
                GameRanking.platform == platform,
                GameRanking.chart_type == chart_type,
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
                GameRanking.chart_type == chart_type,
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
                GameRanking.chart_type == chart_type,
                GameRanking.date.in_(prior_dates),
            )
        )).scalars().all())

        # 历史层：baseline 窗口之外（更早）出现过的 app_id。区分真首发 vs 回归。
        oldest_baseline = min(prior_dates)
        summary["historical_ids"] = set((await db.execute(
            select(distinct(GameRanking.app_id)).where(
                GameRanking.country == country,
                GameRanking.platform == platform,
                GameRanking.chart_type == chart_type,
                GameRanking.date < oldest_baseline,
            )
        )).scalars().all())

        # as_of 当期榜，按名次升序。
        today_rows = (await db.execute(
            select(GameRanking).where(
                GameRanking.country == country,
                GameRanking.platform == platform,
                GameRanking.chart_type == chart_type,
                GameRanking.date == as_of,
            ).order_by(GameRanking.rank.asc().nulls_last())
        )).scalars().all()

    summary["rows"] = [r for r in today_rows if r.app_id not in baseline_ids]
    return summary


def _row_dict(r, *, historical_ids: Optional[set] = None) -> dict:
    """GameRanking → 行字典。给了 historical_ids 时附 `is_reentry` 字段。

    is_reentry=True 表示该 app_id 在 baseline 窗口之前的更早快照里出现过——
    属于"老游戏跌出 baseline 又回来"的回归，不是真首发。digest 据此过滤。
    """
    out = {
        "app_id": r.app_id,
        "name": r.name or r.app_id,
        "publisher": r.publisher,
        "icon_url": r.icon_url,
        "rank": r.rank,
        "revenue": r.revenue,
        "downloads": r.downloads,
    }
    if historical_ids is not None:
        out["is_reentry"] = r.app_id in historical_ids
    return out


async def _load_ignore_keys() -> tuple[set[str], set[str]]:
    """缺口忽略名单 → (publisher corp_squash 键集, app_id 集)。

    与 routers.publishers /gaps **同一名单同一口径**（`publisher_ignores` 表）：
    人工逐条确认的非 SLG 发行商 / 单品。用于把这些**确凿噪声**从全市场新品 feed +
    digest 里剔除。

    与"故意不按 is_slg 过滤"不冲突：is_slg 白名单滞后维护、会漏掉真新厂(如新出海
    SLG)，按它过滤是误杀；忽略名单是人工确认的非 SLG，过滤安全，且**不影响未建档
    的真 SLG**——不在名单里的新厂仍照常浮现。表量级几十行，每次现查开销可忽略。
    """
    from app.models.publisher import PublisherIgnore

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(PublisherIgnore))).scalars().all()
    pub_keys = {r.value for r in rows if r.kind == "publisher"}
    app_ids = {r.value for r in rows if r.kind == "app_id"}
    return pub_keys, app_ids


def _is_ignored(app_id: Optional[str], publisher: Optional[str],
                ignore_pub_keys: set[str], ignore_app_ids: set[str]) -> bool:
    """该行是否被缺口忽略名单覆盖（app_id 精确忽略 或 发行商 corp_squash 命中）。
    口径与 routers.publishers /gaps 完全一致：_tokens（≡ router 的 _toks，均
    `[^a-z0-9]+` 分词）+ corp_squash，保证两处命中同一批 key。"""
    if app_id and app_id in ignore_app_ids:
        return True
    return corp_squash(_tokens(publisher)) in ignore_pub_keys


async def detect_newcomers(
    country: str,
    platform: str,
    *,
    window: Optional[int] = None,
    topn: Optional[int] = None,
    ignore_keys: Optional[tuple[set[str], set[str]]] = None,
    chart_type: str = CHART_GROSSING,
) -> dict:
    """**纯检测**——比对 as_of 当期榜与之前 W 个快照，返回结构化"新面孔"摘要。
    无任何副作用、零 ST 配额，可被 API endpoint 任意频次调用。

    `chart_type` 默认 grossing；传 free 在下载榜上独立检测（ADR 0001 切片 2）。
    `ignore_keys` 可由跨 combo 的调用方预加载一次传入（避免每 combo 重查
    `publisher_ignores`）；不传则本函数自行加载。剔除人工确认的非 SLG 噪声，
    未建档的真新厂不受影响（详见 `_load_ignore_keys`）。
    """
    window = window if window is not None else settings.NEWCOMER_WINDOW
    topn = topn if topn is not None else settings.NEWCOMER_TOPN
    if ignore_keys is None:
        ignore_keys = await _load_ignore_keys()
    ignore_pub_keys, ignore_app_ids = ignore_keys

    base = await _first_appearances(country, platform, window, chart_type)
    historical_ids = base.get("historical_ids")
    summary = {k: v for k, v in base.items() if k not in ("rows", "historical_ids")}
    summary["newcomers"] = [
        {
            **_row_dict(r, historical_ids=historical_ids),
            # 不参与过滤，仅供前端区分"已识别 SLG"vs"新厂商待识别"(后者最值得调研)。
            "is_slg": is_slg(r.app_id, r.publisher),
        }
        for r in base["rows"]
        if r.rank is not None and r.rank <= topn
        and not _is_ignored(r.app_id, r.publisher, ignore_pub_keys, ignore_app_ids)
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


def resolve_entity(app_id: Optional[str], publisher: Optional[str],
                   matchers: list[dict]) -> Optional[str]:
    """(app_id / 发行商串) → 已建档主体的**中文名**，命中不了返回 None。
    复用 detect_publisher_newcomers 的归属规则（钉选 app_id 优先，其次 alias 子序列），
    给市场新面孔 / 异动行补「厂商主体中文归属」用——纯内存匹配，零查询。"""
    pub_tokens = _tokens(publisher)
    for m in matchers:
        if app_id and app_id in m["app_ids"]:
            return m["entity_name"]
        if pub_tokens and any(_kw_hit(pub_tokens, kw) for kw in m["kw_tokens"]):
            return m["entity_name"]
    return None


async def detect_publisher_newcomers(
    country: str,
    platform: str,
    *,
    window: Optional[int] = None,
    matchers: Optional[list[dict]] = None,
    topn: Optional[int] = None,
    chart_type: str = CHART_GROSSING,
) -> dict:
    """已建档厂商主体的新品：首次出现 + 发行商马甲/钉选 app_id 归属到某主体。

    `chart_type` 默认 grossing；传 free 在下载榜上独立检测（ADR 0001 切片 2）。
    与 detect_newcomers 的差异：默认 TopN 阈值更宽松（PUBLISHER_NEWCOMER_TOPN=200
    vs NEWCOMER_TOPN=50）——主体可信，名次较深也值得关注（解决"慢慢爬榜被基线
    见过、永不触发"的漏报，如 Top Heroes），但不再"完全不限名次"（曾让 JP/android
    weekly 抖动产生 #137–#535 长尾刷屏 digest，2026-06-21 实测单 combo 23 项里
    22 项是噪声）。跨 combo 调用时可传入预加载的 matchers 避免重复查主体表。
    """
    window = window if window is not None else settings.NEWCOMER_WINDOW
    topn = topn if topn is not None else settings.PUBLISHER_NEWCOMER_TOPN
    if matchers is None:
        matchers = await _load_entity_matchers()

    base = await _first_appearances(country, platform, window, chart_type)
    historical_ids = base.get("historical_ids")
    summary = {k: v for k, v in base.items() if k not in ("rows", "historical_ids")}
    summary["newcomers"] = []

    # B：baseline 充分性门控。本地快照过少时，"首次出现在本地榜单" ≈ "首次被采到"，
    # 与真实上架日脱钩——次市场（DE/RU 双周同步）刚采集只有 1~2 个快照，会把一整批
    # 老 SLG（2013–2017）误报"新品"。要求 ≥ MIN_BASELINE 个历史快照才报，不足视为
    # 数据积累中（沿用 no_baseline 语义：端点进 combos_without_baseline、digest/落库
    # 拿到空 newcomers）。攒够后由真实上架日门控（端点/digest 层）继续滤老产品。
    if not summary["no_baseline"] and \
            len(summary.get("baseline_dates") or []) < settings.PUBLISHER_NEWCOMER_MIN_BASELINE:
        summary["no_baseline"] = True
        return summary

    for r in base["rows"]:
        if r.rank is None or r.rank > topn:
            continue
        pub_tokens = _tokens(r.publisher)
        for m in matchers:
            if r.app_id in m["app_ids"]:
                matched = "app_id"
            elif pub_tokens and any(_kw_hit(pub_tokens, kw) for kw in m["kw_tokens"]):
                matched = "alias"
            else:
                continue
            summary["newcomers"].append({
                **_row_dict(r, historical_ids=historical_ids),
                "entity_id": m["entity_id"],
                "entity_name": m["entity_name"],
                "matched_by": matched,
            })
            break  # 一个产品归属到第一个命中的主体即可
    return summary


# 富化 miss 时现打免费 lookup 的请求间停顿（秒）。实际 miss 量极小（多为 Android
# 包名，iOS 命中检出历史缓存），礼貌限速即可，与 newcomer_log 落库富化同口径。
_ENRICH_DELAY_S = 1.0


async def gate_publisher_newcomers_by_release_date(
    newcomers: list[dict],
    country: str,
    platform: str,
    *,
    max_age_days: Optional[int] = None,
    enrich_miss: bool = True,
) -> list[dict]:
    """按**真实上架日**给厂商新品二次门控 + 回填 release_date（全零 ST 配额）。

    detect_publisher_newcomers 判「新」用的是本地榜单存在性（首次进本地 game_rankings），
    是真实上线日的零配额代理——但对快照稀疏的次市场会把老产品误报为新品（详见
    config.PUBLISHER_NEWCOMER_MIN_BASELINE / detect_publisher_newcomers docstring）。
    这里用 release_date 兜底：上架早于 N 天前的剔除，N 天内 / 无从判断的保留（缺失
    按新处理、不丢真新品信号——与 itunes_releases._is_old_release 同源哲学）。

    release_date 解析顺序（全免费、零 ST）：
      1) MarketNewcomerLog.release_date —— 检出落库时已富化的缓存（命中率最高）；
      2) PublisherItunesApp.release_date —— 雷达账号下 app（track_id = iOS 数字 app_id）；
      3) enrich_miss=True 时，对仍缺的 app_id 现打一次免费 iTunes/GP lookup（限速）。
    每行回填 `release_date`（供前端展示「新」的判定依据）；返回过滤后的列表。
    """
    if not newcomers:
        return newcomers
    max_age_days = (max_age_days if max_age_days is not None
                    else settings.ITUNES_RELEASES_OLD_RELEASE_DAYS)
    from app.models.newcomer import MarketNewcomerLog
    from app.models.publisher import PublisherItunesApp

    app_ids = [n["app_id"] for n in newcomers if n.get("app_id")]
    rd_by_app: dict[str, str] = {}
    async with AsyncSessionLocal() as db:
        # 1) 检出历史缓存（任一非空 release_date）。
        for aid, rd in (await db.execute(
            select(MarketNewcomerLog.app_id, MarketNewcomerLog.release_date).where(
                MarketNewcomerLog.app_id.in_(app_ids),
                MarketNewcomerLog.release_date.is_not(None),
            )
        )).all():
            rd_by_app.setdefault(aid, rd)
        # 2) 雷达 app 缓存（track_id ≡ iOS 数字 app_id）。
        miss_ids = [a for a in app_ids if a not in rd_by_app]
        if miss_ids:
            for tid, rd in (await db.execute(
                select(PublisherItunesApp.track_id, PublisherItunesApp.release_date).where(
                    PublisherItunesApp.track_id.in_(miss_ids),
                    PublisherItunesApp.release_date.is_not(None),
                )
            )).all():
                rd_by_app.setdefault(tid, rd)

    # 3) 仍缺的 → 免费 lookup（限速）。mock 模式不出外网；失败/拿不到 → 留缺失（保留行）。
    if enrich_miss and not settings.USE_MOCK_DATA:
        from app.services.newcomer_log import enrich_fields
        still_miss = [a for a in app_ids if a not in rd_by_app]
        for i, aid in enumerate(still_miss):
            if i > 0:
                await asyncio.sleep(_ENRICH_DELAY_S)
            data = await enrich_fields(aid, country.lower(), platform.lower())
            if data and data.get("release_date"):
                rd_by_app[aid] = data["release_date"]

    cutoff = (utcnow_naive() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    out = []
    for n in newcomers:
        rd = rd_by_app.get(n.get("app_id"))
        if rd and rd < cutoff:
            continue  # 真实上架日早于阈值 → 老产品，剔除
        out.append({**n, "release_date": rd})
    return out
