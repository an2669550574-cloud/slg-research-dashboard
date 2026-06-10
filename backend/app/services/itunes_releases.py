"""「厂商新品 P2」：iTunes 开发者账号 app 清单 diff——不进榜也能抓到新上架。

数据源 = Apple iTunes lookup API（免费、公开、**非 Sensor Tower**，零 ST 配额）：
GET https://itunes.apple.com/lookup?id=<artistId>&entity=software&country=us&limit=200
返回该开发者账号下全部上架 app（US 商店可见的）。

diff 语义与 newcomers 一致：
- 某账号**首次**同步 → 全量落库标 is_baseline=True，不报"新"（无从判断）。
- 此后同步出现的新 track_id → is_baseline=False = 「App Store 新上架」。
- 已见过的 app 不更新不删除（清单是"见过即留痕"，下架不回收——与素材 cosfs 同哲学）。

节奏：周级调度（scheduler）+ 手动触发端点。账号间 sleep 礼貌限速（Apple 文档口径
约 20 req/min，我们十来个账号、周级，远在红线内）。
"""
import asyncio
import logging
from typing import Optional

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.publisher import PublisherItunesArtist, PublisherItunesApp

logger = logging.getLogger(__name__)

ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"
# 账号间停顿（秒）。十来个账号 × 周级，3s 已极保守。
_POLITE_DELAY_S = 3.0


async def fetch_artist_apps(artist_id: str) -> list[dict]:
    """拉某开发者账号下全部 app。返回 software 结果列表（不含 artist 头记录）。

    失败抛异常由调用方决定跳过还是上抛——sync 循环里单账号失败不拖垮整批。
    """
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(ITUNES_LOOKUP_URL, params={
            "id": artist_id, "entity": "software", "country": "us", "limit": 200,
        })
        resp.raise_for_status()
        data = resp.json()
    return [r for r in data.get("results", []) if r.get("wrapperType") == "software"]


def _app_fields(r: dict) -> dict:
    return {
        "track_id": str(r.get("trackId", "")),
        "name": (r.get("trackName") or "")[:300],
        "bundle_id": r.get("bundleId"),
        "release_date": (r.get("releaseDate") or "")[:10] or None,  # ISO → 日期部分
        "track_view_url": r.get("trackViewUrl"),
    }


async def sync_itunes_releases() -> dict:
    """对全部已挂账号跑一轮清单 diff。返回 {synced, failed, baselined, new_apps}。

    mock 模式不出外网（本地开发用手动端点 + monkeypatch 测试）。
    """
    summary = {"synced": 0, "failed": 0, "baselined": 0, "new_apps": 0}
    if settings.USE_MOCK_DATA:
        logger.info("itunes releases sync skipped (mock mode)")
        return summary

    async with AsyncSessionLocal() as db:
        artists = (await db.execute(select(PublisherItunesArtist))).scalars().all()
    if not artists:
        return summary

    for i, artist in enumerate(artists):
        if i > 0:
            await asyncio.sleep(_POLITE_DELAY_S)
        try:
            apps = await fetch_artist_apps(artist.artist_id)
        except Exception:
            summary["failed"] += 1
            logger.warning("itunes lookup failed for artist %s (%s)",
                           artist.artist_id, artist.label, exc_info=True)
            continue
        result = await ingest_artist_apps(artist.id, apps)
        summary["synced"] += 1
        summary["baselined"] += result["baselined"]
        summary["new_apps"] += result["new_apps"]

    logger.info("itunes releases sync done: %s", summary)
    return summary


async def ingest_artist_apps(artist_row_id: int, apps: list[dict]) -> dict:
    """把一次 lookup 结果落库（diff 核心，可单测）。返回 {baselined, new_apps}。"""
    out = {"baselined": 0, "new_apps": 0}
    async with AsyncSessionLocal() as db:
        artist: Optional[PublisherItunesArtist] = (await db.execute(
            select(PublisherItunesArtist).where(PublisherItunesArtist.id == artist_row_id)
        )).scalar_one_or_none()
        if artist is None:
            return out

        known = set((await db.execute(
            select(PublisherItunesApp.track_id).where(
                PublisherItunesApp.artist_row_id == artist_row_id)
        )).scalars().all())
        first_sync = len(known) == 0

        for r in apps:
            f = _app_fields(r)
            if not f["track_id"] or f["track_id"] in known:
                continue
            known.add(f["track_id"])
            db.add(PublisherItunesApp(
                entity_id=artist.entity_id, artist_row_id=artist_row_id,
                is_baseline=first_sync, **f,
            ))
            out["baselined" if first_sync else "new_apps"] += 1

        artist.last_synced_at = utcnow_naive()
        await db.commit()
    return out
