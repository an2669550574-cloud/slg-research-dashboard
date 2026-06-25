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
from sqlalchemy import delete, select

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.newcomer import MarketNewcomerLog

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
    return {
        "store_url": r.get("trackViewUrl"),
        "release_date": (r.get("releaseDate") or "")[:10] or None,
        "genre": genre,
        "rating": r.get("averageUserRating"),
        "rating_count": r.get("userRatingCount"),
        "price": r.get("formattedPrice"),
        "description": ((r.get("description") or "").strip()[:1500]) or None,
        "screenshot_urls": json.dumps(shots) if shots else None,
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


async def record_market_newcomers(country: str, platform: str) -> dict:
    """检出 → 落库 → 富化一个 combo。返回 {detected, recorded, enriched}。

    两路口径**取并集**落库（按 app_id 去重）：
    - 市场宽口径 detect_newcomers（NEWCOMER_HISTORY_TOPN，默认 100）——全市场新面孔。
    - 已建档主体 detect_publisher_newcomers（PUBLISHER_NEWCOMER_TOPN，默认 200）——
      主体可信、名次更深也留底，专门接住「冷启动名次深于 100、慢爬进榜时已被基线
      吞掉」的漏报（如 Century Games《Top General》首见 rank 144 > 100 永不入库）。
    页面有 Top50/Top100 筛选，日报推送口径（Top50）不受本函数影响。
    """
    out = {"detected": 0, "recorded": 0, "enriched": 0}
    from app.services.newcomers import detect_newcomers, detect_publisher_newcomers
    from app.services.slg_publishers import is_slg
    market = await detect_newcomers(country, platform,
                                    topn=settings.NEWCOMER_HISTORY_TOPN)
    publisher = await detect_publisher_newcomers(country, platform)  # topn=200 默认
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

    async with AsyncSessionLocal() as db:
        seen = set((await db.execute(
            select(MarketNewcomerLog.app_id).where(
                MarketNewcomerLog.country == country,
                MarketNewcomerLog.platform == platform,
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
                as_of=as_of, name=n["name"], publisher=n.get("publisher"),
                icon_url=n.get("icon_url"), rank=n.get("rank"),
                revenue=n.get("revenue"), is_slg=bool(n.get("is_slg")),
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
        await db.commit()
    if out["recorded"]:
        logger.info("newcomer log %s/%s: %s", country, platform, out)
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


async def attribute_entities(rows) -> dict[int, tuple[int, str]]:
    """读时归属：log 行 → 已建档主体。{row.id: (entity_id, entity_name)}。

    复用 newcomers._load_entity_matchers（与「厂商新品」同一套归属口径）。
    **读时计算**而非落库——建档发生在检出之后，存档会过期；活算让
    「建档 → 历史卡片立刻显示已归属」零回写。量级几十主体，开销可忽略。
    """
    from app.services.newcomers import _kw_hit, _load_entity_matchers
    from app.services.slg_publishers import _tokens

    matchers = await _load_entity_matchers()
    out: dict[int, tuple[int, str]] = {}
    for r in rows:
        pub_tokens = _tokens(r.publisher)
        for m in matchers:
            if r.app_id in m["app_ids"] or (
                pub_tokens and any(_kw_hit(pub_tokens, kw) for kw in m["kw_tokens"])
            ):
                out[r.id] = (m["entity_id"], m["entity_name"])
                break
    return out
