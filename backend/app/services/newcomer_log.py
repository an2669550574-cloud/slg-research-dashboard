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
from sqlalchemy import select

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
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(ITUNES_LOOKUP_URL, params={
            "id": app_id, "country": country.lower(), "entity": "software"})
        resp.raise_for_status()
        results = [r for r in resp.json().get("results", [])
                   if r.get("wrapperType") == "software"]
    if not results:
        return None
    r = results[0]
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
    return {
        "store_url": r.get("trackViewUrl"),
        "release_date": None,  # GP 页拿不到稳定的上架日
        "genre": genres[0] if genres else None,
        "rating": r.get("averageUserRating"),
        "rating_count": r.get("userRatingCount"),
        "price": r.get("formattedPrice"),
        "description": ((r.get("description") or "").strip()[:1500]) or None,
        "screenshot_urls": None,
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

    detect 用 NEWCOMER_HISTORY_TOPN（默认 100，比日报的 Top50 宽）——历史沉淀
    宁可多收，页面有 Top50/Top100 筛选；日报口径不受影响。
    """
    out = {"detected": 0, "recorded": 0, "enriched": 0}
    from app.services.newcomers import detect_newcomers
    summary = await detect_newcomers(country, platform,
                                     topn=settings.NEWCOMER_HISTORY_TOPN)
    newcomers = summary.get("newcomers") or []
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
                as_of=summary["as_of"], name=n["name"], publisher=n.get("publisher"),
                icon_url=n.get("icon_url"), rank=n.get("rank"),
                revenue=n.get("revenue"), is_slg=bool(n.get("is_slg")),
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
