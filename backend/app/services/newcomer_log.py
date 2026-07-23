"""新面孔检出落库 + 免费源富化（新品监测 v2 的持久层）。

- record_market_newcomers(country, platform)：跑一次 detect_newcomers
  （口径 NEWCOMER_HISTORY_TOPN，比日报的 TopN 宽），把**未见过**的
  (country, platform, app_id) 落库并富化。已落库的不重写（首报即定格）。
- 富化全免费零 ST：iOS app_id 是数字 trackId → iTunes lookup；Android app_id
  是 GP 包名 → 复用 gp_releases 的页面 JSON-LD 解析。失败留 NULL 不丢检出。
- 调用点：定时同步成功后（scheduler._scheduled_sync）+ 手动回填端点。
  手动 refresh 榜单不触发（与告警同哲学，避免重复写）。
"""
import asyncio
import json
import logging
from typing import Optional

import httpx
from datetime import timedelta
from sqlalchemy import delete, select, update

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.newcomer import MarketNewcomerLog
from app.models.game import CHART_GROSSING, CHART_FREE

logger = logging.getLogger(__name__)

_POLITE_DELAY_S = 2.0


async def _enrich_ios(app_id: str, country: str) -> Optional[dict]:
    """iTunes lookup（免费、非 ST）按 trackId 取详情。非数字 id 直接放弃。"""
    if not app_id.isdigit():
        return None
    from app.services.itunes_releases import ITUNES_LOOKUP_URL
    # 检出国 miss 时退避 us/sg 再查——软启动产品常不在检出国的 storefront 可见。
    r = None
    async with httpx.AsyncClient(timeout=20) as client:
        for cc in dict.fromkeys([country.lower(), "us", "sg"]):
            resp = await client.get(ITUNES_LOOKUP_URL, params={
                "id": app_id, "country": cc, "entity": "software"})
            resp.raise_for_status()
            results = [x for x in resp.json().get("results", [])
                       if x.get("wrapperType") == "software"]
            if results:
                r = results[0]
                break
            await asyncio.sleep(1)
    if r is None:
        return None
    genres = r.get("genres") or []
    genre = next((g for g in genres if g and g != "Games"), None) or r.get("primaryGenreName")
    shots = [u for u in (r.get("screenshotUrls") or []) if isinstance(u, str)][:5]
    langs = [c for c in (r.get("languageCodesISO2A") or []) if isinstance(c, str)][:30]
    return {
        "store_url": r.get("trackViewUrl"),
        "release_date": (r.get("releaseDate") or "")[:10] or None,
        "genre": genre,
        "rating": r.get("averageUserRating"),
        "rating_count": r.get("userRatingCount"),
        "price": r.get("formattedPrice"),
        "description": ((r.get("description") or "").strip()[:1500]) or None,
        "screenshot_urls": json.dumps(shots) if shots else None,
        "version": r.get("version"),
        "current_version_date": (r.get("currentVersionReleaseDate") or "")[:10] or None,
        "languages": ",".join(langs) or None,
        "enrich_source": "itunes",
    }


async def _enrich_android(app_id: str) -> Optional[dict]:
    """Android app_id = GP 包名 → 复用 gp_releases 的详情页 JSON-LD 解析。"""
    from app.services.gp_releases import _get_html, app_page_url, parse_app_detail
    async with httpx.AsyncClient(timeout=20) as client:
        html = await _get_html(client, app_page_url(app_id))
    r = parse_app_detail(html, app_id)
    if r.get("trackName") == app_id and "description" not in r:
        return None  # 降级到仅包名 = 没解析出任何详情
    genres = r.get("genres") or []
    shots = [u for u in (r.get("screenshotUrls") or []) if isinstance(u, str)][:5]
    return {
        "store_url": r.get("trackViewUrl"),
        "release_date": None,  # GP 页拿不到稳定的上架日
        "genre": genres[0] if genres else None,
        "rating": r.get("averageUserRating"),
        "rating_count": r.get("userRatingCount"),
        "price": r.get("formattedPrice"),
        "description": ((r.get("description") or "").strip()[:1500]) or None,
        "screenshot_urls": json.dumps(shots) if shots else None,
        "enrich_source": "gp",
    }


async def enrich_fields(app_id: str, country: str, platform: str) -> Optional[dict]:
    """按平台路由免费富化源。任何失败返回 None（调用方留 NULL 不丢检出）。"""
    try:
        if platform == "ios":
            return await _enrich_ios(app_id, country)
        return await _enrich_android(app_id)
    except Exception:
        logger.warning("newcomer enrich failed for %s/%s %s",
                       country, platform, app_id, exc_info=True)
        return None


async def slg_app_ids_known(app_ids: Optional[set[str]] = None) -> set[str]:
    """app_id 级 SLG 记忆：任一 log 行 is_slg=1，或已是 tracked game 的 app_id 集合。

    is_slg() 是按 publisher 串逐行判定的——次市场商店返回本地化厂商名（韩/日/俄文），
    同一游戏跨 combo 判定会分裂（Last Furry KR-ios=1 / JP-ios=0 实锤）。iOS 数字
    app_id 与 GP 包名都全球唯一，「任一 combo 判过 SLG」按 app_id OR 传播天然安全。
    消费方：落库（新行继承记忆）、digest（拼卡前统一回写）、/history 读端、
    subgenre 回补候选、视频 SLG 门控。传 app_ids 时只查这批（省一次全表扫）。
    """
    from app.models.game import Game
    async with AsyncSessionLocal() as db:
        q = select(MarketNewcomerLog.app_id).where(MarketNewcomerLog.is_slg.is_(True)).distinct()
        gq = select(Game.app_id)
        if app_ids is not None:
            if not app_ids:
                return set()
            q = q.where(MarketNewcomerLog.app_id.in_(app_ids))
            gq = gq.where(Game.app_id.in_(app_ids))
        out = set((await db.execute(q)).scalars().all())
        out |= set((await db.execute(gq)).scalars().all())
    return out


async def _record_one_chart(country: str, platform: str, chart_type: str) -> dict:
    """单个榜（chart_type）的检出 → 落库 → 富化。返回 {detected, recorded, enriched}。

    两路口径**取并集**落库（按 app_id 去重）：
    - 市场宽口径 detect_newcomers（NEWCOMER_HISTORY_TOPN，默认 100）——全市场新面孔。
    - 已建档主体 detect_publisher_newcomers（PUBLISHER_NEWCOMER_TOPN，默认 200）——
      主体可信、名次更深也留底，专门接住「冷启动名次深于 100、慢爬进榜时已被基线
      吞掉」的漏报（如 Century Games《Top General》首见 rank 144 > 100 永不入库）。
    dedup / 落库均按 chart_type 隔离，收入榜与下载榜各自留一条。
    """
    out = {"detected": 0, "recorded": 0, "enriched": 0}
    from app.services.newcomers import detect_newcomers, detect_publisher_newcomers
    from app.services.slg_publishers import is_slg
    market = await detect_newcomers(country, platform,
                                    topn=settings.NEWCOMER_HISTORY_TOPN,
                                    chart_type=chart_type)
    publisher = await detect_publisher_newcomers(country, platform, chart_type=chart_type)
    # 市场行已带 is_slg；合并时市场优先，主体独有行补算 is_slg 后并入。
    merged: dict[str, dict] = {n["app_id"]: n for n in (market.get("newcomers") or [])}
    for n in (publisher.get("newcomers") or []):
        if n["app_id"] not in merged:
            merged[n["app_id"]] = {**n, "is_slg": is_slg(n["app_id"], n.get("publisher"))}
    newcomers = list(merged.values())
    as_of = market.get("as_of") or publisher.get("as_of")
    out["detected"] = len(newcomers)
    if not newcomers:
        return out

    # app_id 级 SLG 记忆（跨 combo/跨榜/跨天）：别的 combo 曾判 SLG 或已 tracked 的，
    # 本 combo 本地化 publisher 串命不中白名单也照样标 1——治跨 combo is_slg 分裂。
    known_slg = await slg_app_ids_known({n["app_id"] for n in newcomers})

    async with AsyncSessionLocal() as db:
        seen = set((await db.execute(
            select(MarketNewcomerLog.app_id).where(
                MarketNewcomerLog.country == country,
                MarketNewcomerLog.platform == platform,
                MarketNewcomerLog.chart_type == chart_type,
                MarketNewcomerLog.app_id.in_([n["app_id"] for n in newcomers]),
            )
        )).scalars().all())

        for i, n in enumerate(nc for nc in newcomers if nc["app_id"] not in seen):
            if i > 0:
                await asyncio.sleep(_POLITE_DELAY_S)
            enriched = None
            if not settings.USE_MOCK_DATA:
                enriched = await enrich_fields(n["app_id"], country, platform)
            row = MarketNewcomerLog(
                country=country, platform=platform, app_id=n["app_id"],
                chart_type=chart_type,
                as_of=as_of, name=n["name"], publisher=n.get("publisher"),
                icon_url=n.get("icon_url"), rank=n.get("rank"),
                revenue=n.get("revenue"),
                is_slg=bool(n.get("is_slg")) or n["app_id"] in known_slg,
                # PR #93+0022：固化检出时的真首发 vs 回归判断（None = no_baseline 路径
                # 或 detect 缺字段；前端把缺省/None 当真首发处理）。
                is_reentry=n.get("is_reentry"),
                **(enriched or {}),
            )
            if enriched:
                row.enriched_at = utcnow_naive()
                out["enriched"] += 1
            db.add(row)
            out["recorded"] += 1
        # 前进式对齐（log 自愈）：本轮 live 判 SLG 的 app，其既有行（更早写入的别的
        # combo/榜，当时本地化名 miss 标了 0）一并置 1——存量分裂随每轮检出收敛。
        live_slg = {n["app_id"] for n in newcomers if n.get("is_slg")}
        if live_slg:
            await db.execute(
                update(MarketNewcomerLog)
                .where(MarketNewcomerLog.app_id.in_(live_slg),
                       MarketNewcomerLog.is_slg.is_(False))
                .values(is_slg=True))
        await db.commit()
    if out["recorded"]:
        logger.info("newcomer log %s/%s/%s: %s", country, platform, chart_type, out)
    return out


async def record_market_newcomers(country: str, platform: str) -> dict:
    """一个 combo 的检出沉淀：收入榜恒跑；开了下载榜的 combo（FREE_CHART_COMBOS）
    额外跑一遍下载榜（ADR 0001 切片 2）。两榜各自检测/落库，合计返回。"""
    out = await _record_one_chart(country, platform, CHART_GROSSING)
    if (country, platform) in settings.free_chart_combos_set:
        free = await _record_one_chart(country, platform, CHART_FREE)
        for k in out:
            out[k] += free[k]
    return out


async def record_all_combos() -> dict:
    """全 combo 跑一轮检出落库（手动回填端点用）。"""
    total = {"detected": 0, "recorded": 0, "enriched": 0}
    for country, platform in settings.sync_combos_list:
        try:
            r = await record_market_newcomers(country, platform)
            for k in total:
                total[k] += r[k]
        except Exception:
            logger.exception("newcomer record failed for %s/%s", country, platform)
    return total


# 雷达影子行的检出通道标记（chart_type）。不是真榜——纯为 riding 中文化/subgenre/视频
# 富化管道而存在；/history 显式排除它，故不进新品页市场卡片网格（P1-1）。
CHART_RADAR = "radar"


async def record_radar_newcomers(rows: list[dict]) -> int:
    """把商店雷达检出的 SLG 真新上架写成 market_newcomer_log「影子行」（chart_type='radar'）。

    目的（P1-1，修「信号越早富化越少」倒挂）：软启动期新品是买量调研最佳观察窗，此前只落
    `publisher_itunes_apps`、拿不到中文摘要/subgenre/视频。写影子行后天然被 translate /
    subgenre backfill / 视频 drain（都源自 market_newcomer_log、不按 chart_type 过滤）捡起。

    富化字段（description/genre/rating/icon/store_url…）本就随 iTunes lookup 拿到，直接落库；
    只 summary_cn/subgenre_cn/description_cn 留 NULL 待 LLM drain。rank/revenue=NULL（非榜）、
    is_slg=True（已 SLG 门控）。(country,platform,app_id,'radar') 唯一——已存在则跳过（幂等，
    雷达重扫不会重复；真新上架本就只在首检出触发一次）。零 ST。
    """
    if not rows:
        return 0
    today = utcnow_naive().strftime("%Y-%m-%d")
    now = utcnow_naive()
    written = 0
    async with AsyncSessionLocal() as db:
        for r in rows:
            app_id = r.get("track_id") or r.get("app_id")
            if not app_id:
                continue
            country = (r.get("country") or "WW").upper()
            platform = r.get("platform") or "ios"
            exists = (await db.execute(
                select(MarketNewcomerLog.id).where(
                    MarketNewcomerLog.country == country,
                    MarketNewcomerLog.platform == platform,
                    MarketNewcomerLog.app_id == app_id,
                    MarketNewcomerLog.chart_type == CHART_RADAR,
                ))).scalar_one_or_none()
            if exists is not None:
                continue
            db.add(MarketNewcomerLog(
                country=country, platform=platform, app_id=app_id, chart_type=CHART_RADAR,
                as_of=today, name=r.get("name") or app_id, publisher=r.get("publisher"),
                icon_url=r.get("artwork_url"), rank=None, revenue=None, is_slg=True,
                is_reentry=False, first_detected_at=now,
                store_url=r.get("track_view_url"), release_date=r.get("release_date"),
                genre=r.get("genre"), rating=r.get("rating"), rating_count=r.get("rating_count"),
                price=r.get("price"), description=r.get("description"),
                screenshot_urls=r.get("screenshot_urls"), languages=r.get("languages"),
                enrich_source=("gp" if platform == "android" else "itunes"), enriched_at=now,
            ))
            written += 1
        if written:
            await db.commit()
    if written:
        logger.info("radar newcomers: %d shadow row(s) written to market_newcomer_log", written)
    return written


CHART_DISCOVERY = "discovery"


async def record_discovery_newcomers(rows: list[dict]) -> int:
    """把**人工线报核实**的未追踪主体新品写成 market_newcomer_log「影子行」（chart_type='discovery'）。

    与 radar/rss 影子行同哲学（`record_radar_newcomers`）：写库后天然被 translate / subgenre /
    视频 drain 捡起（都源自 market_newcomer_log、不按 chart_type 过滤），次日带中文摘要 / 子品类
    进维护者卡【📮 发现层线报】段。区别只在源=人给的线报（发现层切片1 分诊工具的落库出口 B），
    is_slg 由人工确认（True）。rank/revenue=NULL（非榜）。(country,platform,app_id,'discovery') 唯一
    → 幂等（重复确认不重写）。**零 ST**——富化字段来自免费 enrich，不碰 game_rankings。

    入参每行（来自分诊 draft/enrich）：app_id, platform, 可选 country(默认 WW)/name/publisher/
    genre/description/store_url/rating/rating_count/release_date/subgenre_cn/is_slg(默认 True)。"""
    if not rows:
        return 0
    today = utcnow_naive().strftime("%Y-%m-%d")
    now = utcnow_naive()
    written = 0
    async with AsyncSessionLocal() as db:
        for r in rows:
            app_id = r.get("app_id")
            if not app_id:
                continue
            country = (r.get("country") or "WW").upper()
            platform = r.get("platform") or "android"
            exists = (await db.execute(
                select(MarketNewcomerLog.id).where(
                    MarketNewcomerLog.country == country,
                    MarketNewcomerLog.platform == platform,
                    MarketNewcomerLog.app_id == app_id,
                    MarketNewcomerLog.chart_type == CHART_DISCOVERY,
                ))).scalar_one_or_none()
            if exists is not None:
                continue
            db.add(MarketNewcomerLog(
                country=country, platform=platform, app_id=app_id, chart_type=CHART_DISCOVERY,
                as_of=today, name=r.get("name") or app_id, publisher=r.get("publisher"),
                rank=None, revenue=None, is_slg=bool(r.get("is_slg", True)), is_reentry=False,
                first_detected_at=now, store_url=r.get("store_url"),
                release_date=r.get("release_date"), genre=r.get("genre"),
                rating=r.get("rating"), rating_count=r.get("rating_count"),
                description=r.get("description"), subgenre_cn=r.get("subgenre_cn"),
                enrich_source=("gp" if platform == "android" else "itunes"), enriched_at=now,
            ))
            written += 1
        if written:
            await db.commit()
    if written:
        logger.info("discovery newcomers: %d shadow row(s) written to market_newcomer_log", written)
    return written


async def prune_newcomer_log(retention_days: Optional[int] = None) -> int:
    """删除 first_detected_at 早于保留窗口的检出日志，返回删除行数。

    market_newcomer_log 检出即落库、只增不减——读路径只按 days 筛、不影响表大小。
    每日定时跑一次（scheduler），把超过 NEWCOMER_LOG_RETENTION_DAYS 的老行清掉，
    避免表无限膨胀。retention<=0 视为关闭（永久保留），直接返回 0 不删。
    """
    days = settings.NEWCOMER_LOG_RETENTION_DAYS if retention_days is None else retention_days
    if days <= 0:
        return 0
    cutoff = utcnow_naive() - timedelta(days=days)
    async with AsyncSessionLocal() as db:
        deleted = (await db.execute(
            delete(MarketNewcomerLog).where(MarketNewcomerLog.first_detected_at < cutoff)
        )).rowcount
        await db.commit()
    if deleted:
        logger.info("newcomer log prune: deleted %d rows older than %d days", deleted, days)
    return deleted or 0


async def attribute_entities(rows) -> dict[int, tuple[int, str, bool]]:
    """读时归属：log 行 → 已建档主体。{row.id: (entity_id, entity_name, entity_is_slg)}。

    复用 newcomers._load_entity_matchers（与「厂商新品」同一套归属口径）。
    **读时计算**而非落库——建档发生在检出之后，存档会过期；活算让
    「建档 → 历史卡片立刻显示已归属」零回写。量级几十主体，开销可忽略。
    第三元 entity_is_slg：归属展示对全部实体生效，但把「已归属」当 SLG 信号
    用时必须过它（is_slg=False 的调研/资本系档案不算竞品）。
    """
    from app.services.newcomers import _kw_hit, _load_entity_matchers
    from app.services.slg_publishers import _tokens

    matchers = await _load_entity_matchers()
    out: dict[int, tuple[int, str, bool]] = {}
    for r in rows:
        pub_tokens = _tokens(r.publisher)
        for m in matchers:
            if r.app_id in m["app_ids"] or (
                pub_tokens and any(_kw_hit(pub_tokens, kw) for kw in m["kw_tokens"])
            ):
                out[r.id] = (m["entity_id"], m["entity_name"], m["is_slg"])
                break
    return out
