"""「厂商新品 P2」：iTunes 开发者账号 app 清单 diff——不进榜也能抓到新上架。

数据源 = Apple iTunes lookup API（免费、公开、**非 Sensor Tower**，零 ST 配额）：
GET https://itunes.apple.com/lookup?id=<artistId>&entity=software&country=<sf>&limit=200
返回该开发者账号下、**该 storefront 可见**的全部 app。

多区扫描（2026-06-11 升级）：SLG 几乎都先软启动（PH/CA/AU/SG 等区先上、美区
后上），单扫 us 在软启动期完全失明。每轮按 ITUNES_RELEASES_STOREFRONTS 逐区
拉取、按 track_id 合并，storefronts 列记录可见区并每轮取并集刷新：
- 新 track_id 且 release_date 在 ITUNES_RELEASES_OLD_RELEASE_DAYS 内 → 新上架；
- 新 track_id 但 release_date 太老 → 静默入基线（新增扫描区首轮的历史区域
  限定 app / 重新上架的老包，不是"新品"，不刷屏）；
- 已有非基线行**新增**了可见区 → 「扩区上线」（软启动 → 更大范围/美区），
  随同轮 digest 一并推送。

diff 语义与 newcomers 一致：
- 某账号**首次**同步 → 全量落库标 is_baseline=True，不报"新"（无从判断）。
- 已见过的 app 不删除（清单是"见过即留痕"，下架不回收——与素材 cosfs 同哲学）。

节奏：日级调度（scheduler）+ 手动触发端点。请求间 sleep 礼貌限速（Apple 口径
约 20 req/min；21 账号 × 5 区 × 3s ≈ 5 分钟/轮，日级远在红线内）。
"""
import asyncio
import json
import logging
from typing import Optional

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.publisher import PublisherItunesArtist, PublisherItunesApp

logger = logging.getLogger(__name__)

ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"
# 请求间停顿（秒）。多区扫描后是「每请求」不是「每账号」。
_POLITE_DELAY_S = 3.0
# 合并多区结果时 storefront 的稳定排序（us 永远排最前，便于一眼看「美区是否可见」）。
_SF_SEEN_KEY = "_seen_storefronts"
# 已存在行的展示字段自愈白名单：仅当行内为空、新 lookup 有值时回填（不含 name/storefronts
# 等身份/状态字段）。早期基线行无 artwork 的历史缺口由此逐轮补齐。
_DISPLAY_BACKFILL_FIELDS = (
    "artwork_url", "genre", "rating", "rating_count", "price",
    "description", "screenshot_urls", "languages", "track_view_url",
    "bundle_id", "release_date",
)


def _configured_storefronts() -> list[str]:
    out = []
    for raw in (settings.ITUNES_RELEASES_STOREFRONTS or "us").split(","):
        sf = raw.strip().lower()
        if sf and sf not in out:
            out.append(sf)
    return out or ["us"]


def _sf_sorted(storefronts: set[str]) -> list[str]:
    order = {sf: i for i, sf in enumerate(_configured_storefronts())}
    return sorted(storefronts, key=lambda s: (order.get(s, 99), s))


async def fetch_artist_apps(artist_id: str, country: str = "us") -> list[dict]:
    """拉某开发者账号下、某 storefront 可见的全部 app。

    失败抛异常由调用方决定跳过还是上抛——sync 循环里单账号失败不拖垮整批。
    """
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(ITUNES_LOOKUP_URL, params={
            "id": artist_id, "entity": "software", "country": country, "limit": 200,
        })
        resp.raise_for_status()
        data = resp.json()
    return [r for r in data.get("results", []) if r.get("wrapperType") == "software"]


async def fetch_artist_apps_multi(artist_id: str) -> list[dict]:
    """逐 storefront 拉取并按 trackId 合并；每条记录带 _seen_storefronts。

    单区失败只丢那一区的可见性（记 warning），不拖垮整账号——「在哪些区可见」
    本来就是逐区采样，缺一区等于那一区暂时失明，下一轮自然补回。
    全部区都失败才向上抛（整账号计 failed）。
    """
    merged: dict[str, dict] = {}
    ok_any = False
    last_err: Optional[Exception] = None
    for i, sf in enumerate(_configured_storefronts()):
        if i > 0:
            await asyncio.sleep(_POLITE_DELAY_S)
        try:
            apps = await fetch_artist_apps(artist_id, country=sf)
        except Exception as e:
            last_err = e
            logger.warning("itunes lookup failed for artist %s storefront %s", artist_id, sf)
            continue
        ok_any = True
        for r in apps:
            tid = str(r.get("trackId", ""))
            if not tid:
                continue
            if tid in merged:
                merged[tid][_SF_SEEN_KEY].add(sf)
            else:
                r[_SF_SEEN_KEY] = {sf}
                merged[tid] = r
    if not ok_any:
        raise last_err or RuntimeError(f"all storefront lookups failed for {artist_id}")
    return list(merged.values())


# 反解开发者账号时逐区试的 storefront：app 区域限定时单 us 会失明——日韩/中国限定的
# iOS app 常不上美区 App Store（实测 6waves/gumi/星辉/英雄互娱 全是日区限定，us 反解
# 全失败）。命中任一区即返回（同一 app 的 artistId 跨区一致）；us 优先（多数命中即停），
# 日韩中港台兜底。改这个列表后「雷达覆盖建议」的反解覆盖面随之变。
_ARTIST_RESOLVE_STOREFRONTS = ("us", "jp", "kr", "cn", "tw", "hk")
# 逐区之间的礼貌停顿（仅在前一区未命中、需往下试时才发生；多数 app us 即命中不 sleep）。
_ARTIST_RESOLVE_DELAY_S = 0.5


async def resolve_artist_for_app(app_id: str) -> Optional[dict]:
    """反向解析：iTunes lookup **按 app track id** 查 → 该 app 的开发者账号 (artistId, artistName)。

    用于「雷达覆盖建议」——主体已钉了 iOS 数字 app_id，但还没接开发者账号雷达时，
    从这个 app 免费反解出 artistId 供一键接入。仅对 iOS 数字 app_id 有效（Android
    包名 / 空 → None）。**逐 storefront 兜底**（`_ARTIST_RESOLVE_STOREFRONTS`）：app 区域
    限定时单 us 会失明，按区试到第一个能看到该 app + 有 artistId 的区即返回。免费、公开、
    零 ST 配额。全区都查不到 / 无 artistId → None。
    返回 {"artist_id": str, "artist_name": str|None, "app_name": str|None}。
    """
    if not app_id or not app_id.isdigit():
        return None
    for i, sf in enumerate(_ARTIST_RESOLVE_STOREFRONTS):
        if i:
            await asyncio.sleep(_ARTIST_RESOLVE_DELAY_S)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(ITUNES_LOOKUP_URL, params={
                    "id": app_id, "entity": "software", "country": sf,
                })
                resp.raise_for_status()
                results = resp.json().get("results", [])
        except Exception:
            logger.warning("itunes artist resolve failed for app %s storefront %s", app_id, sf)
            continue
        soft = [r for r in results if r.get("wrapperType") == "software"]
        if soft and soft[0].get("artistId"):
            return {
                "artist_id": str(soft[0]["artistId"]),
                "artist_name": soft[0].get("artistName"),
                "app_name": soft[0].get("trackName"),
            }
    return None


def _app_fields(r: dict) -> dict:
    # genre：genres[] 第一个非 "Games" 的子品类（Strategy/Puzzle…）比 primaryGenreName
    # ("Games") 有信息量，便于分级新上架是否 SLG；都没有时回退 primaryGenreName。
    genres = r.get("genres") or []
    genre = next((g for g in genres if g and g != "Games"), None) or r.get("primaryGenreName")
    shots = [u for u in (r.get("screenshotUrls") or []) if isinstance(u, str)][:5]
    langs = [c for c in (r.get("languageCodesISO2A") or []) if isinstance(c, str)][:30]
    return {
        "track_id": str(r.get("trackId", "")),
        "name": (r.get("trackName") or "")[:300],
        "bundle_id": r.get("bundleId"),
        "release_date": (r.get("releaseDate") or "")[:10] or None,  # ISO → 日期部分
        "track_view_url": r.get("trackViewUrl"),
        # 以下字段均出自同一免费 lookup 响应，零增量 ST 配额（纯展示/详情）
        "artwork_url": r.get("artworkUrl512") or r.get("artworkUrl100"),
        "genre": genre,
        "rating": r.get("averageUserRating"),
        "rating_count": r.get("userRatingCount"),
        "price": r.get("formattedPrice"),
        "description": ((r.get("description") or "").strip()[:1500]) or None,
        "screenshot_urls": json.dumps(shots) if shots else None,
        "languages": ",".join(langs) or None,
    }


def _is_old_release(release_date: Optional[str]) -> bool:
    """release_date 早于 OLD_RELEASE_DAYS 天前 → 不算"新上架"。缺失按新处理（不丢信号）。"""
    if not release_date:
        return False
    from datetime import timedelta
    cutoff = (utcnow_naive() - timedelta(days=settings.ITUNES_RELEASES_OLD_RELEASE_DAYS))
    return release_date < cutoff.strftime("%Y-%m-%d")


async def sync_itunes_releases() -> dict:
    """对全部已挂账号跑一轮多区清单 diff。

    返回 {synced, failed, baselined, new_apps, backfilled_old, expanded}。
    mock 模式不出外网（本地开发用手动端点 + monkeypatch 测试）。
    """
    summary = {"synced": 0, "failed": 0, "baselined": 0,
               "new_apps": 0, "backfilled_old": 0, "expanded": 0, "enriched": 0}
    if settings.USE_MOCK_DATA:
        logger.info("itunes releases sync skipped (mock mode)")
        return summary

    async with AsyncSessionLocal() as db:
        # 只取 iOS 账号——GP 账号（platform='gp'）由 gp_releases.sync_gp_releases
        # 负责，丢给 iTunes lookup 会 400。
        artists = (await db.execute(
            select(PublisherItunesArtist).where(PublisherItunesArtist.platform == "ios")
        )).scalars().all()
    if not artists:
        return summary

    started_at = utcnow_naive()
    expanded_rows: list[tuple[int, list[str]]] = []  # (app row id, 新增的区)
    for i, artist in enumerate(artists):
        if i > 0:
            await asyncio.sleep(_POLITE_DELAY_S)
        try:
            apps = await fetch_artist_apps_multi(artist.artist_id)
        except Exception:
            summary["failed"] += 1
            logger.warning("itunes lookup failed for artist %s (%s)",
                           artist.artist_id, artist.label, exc_info=True)
            continue
        result = await ingest_artist_apps(artist.id, apps)
        summary["synced"] += 1
        for k in ("baselined", "new_apps", "backfilled_old", "expanded", "enriched"):
            summary[k] += result[k]
        expanded_rows.extend(result["expanded_rows"])

    logger.info("itunes releases sync done: %s", summary)
    if summary["new_apps"] > 0 or expanded_rows:
        # 本轮有新上架或扩区 → 钉钉汇总。告警是旁路，失败不影响同步结果。
        from app.services.release_alerts import alert_appstore_releases
        try:
            await alert_appstore_releases(since=started_at, expanded=expanded_rows)
        except Exception:
            logger.exception("App Store releases DingTalk alert failed (sync itself succeeded)")
    return summary


async def ingest_artist_apps(artist_row_id: int, apps: list[dict]) -> dict:
    """把一次（多区合并后的）lookup 结果落库（diff 核心，可单测）。

    返回 {baselined, new_apps, backfilled_old, expanded, expanded_rows}。
    输入记录可带 _seen_storefronts（set）；不带视作 {"us"}（兼容单区调用/旧测试）。
    """
    out = {"baselined": 0, "new_apps": 0, "backfilled_old": 0,
           "expanded": 0, "expanded_rows": [], "enriched": 0}
    async with AsyncSessionLocal() as db:
        artist: Optional[PublisherItunesArtist] = (await db.execute(
            select(PublisherItunesArtist).where(PublisherItunesArtist.id == artist_row_id)
        )).scalar_one_or_none()
        if artist is None:
            return out

        existing: dict[str, PublisherItunesApp] = {
            row.track_id: row
            for row in (await db.execute(
                select(PublisherItunesApp).where(
                    PublisherItunesApp.artist_row_id == artist_row_id)
            )).scalars().all()
        }
        first_sync = len(existing) == 0

        for r in apps:
            seen_sfs: set[str] = set(r.get(_SF_SEEN_KEY) or {"us"})
            f = _app_fields(r)
            tid = f["track_id"]
            if not tid:
                continue

            row = existing.get(tid)
            if row is not None:
                # 已见过：可见区并集刷新；非基线行新增了区 = 扩区上线。
                old_sfs = set((row.storefronts or "").split(",")) - {""}
                added = seen_sfs - old_sfs
                if added:
                    row.storefronts = ",".join(_sf_sorted(old_sfs | seen_sfs))
                    if not row.is_baseline and old_sfs:
                        out["expanded"] += 1
                        out["expanded_rows"].append((row.id, _sf_sorted(added)))
                # 展示字段自愈：当前为空、新 lookup 有值就回填（早期基线行无 artwork 等
                # 历史缺口逐轮补齐；只填空、绝不覆盖已有值）。
                for k in _DISPLAY_BACKFILL_FIELDS:
                    nv = f.get(k)
                    if nv not in (None, "") and not getattr(row, k):
                        setattr(row, k, nv)
                        out["enriched"] += 1
                continue

            # 基线之后首次见到、但上架日期太老 → 静默入基线（新增扫描区首轮的
            # 历史区域限定 app / 重新上架的老包，不是"新品"）。
            silent_old = (not first_sync) and _is_old_release(f["release_date"])
            new_row = PublisherItunesApp(
                entity_id=artist.entity_id, artist_row_id=artist_row_id,
                is_baseline=first_sync or silent_old,
                storefronts=",".join(_sf_sorted(seen_sfs)),
                **f,
            )
            db.add(new_row)
            existing[tid] = new_row
            if first_sync:
                out["baselined"] += 1
            elif silent_old:
                out["backfilled_old"] += 1
            else:
                out["new_apps"] += 1

        artist.last_synced_at = utcnow_naive()
        await db.commit()
    return out
