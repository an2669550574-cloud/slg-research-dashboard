"""「厂商新品」GP 侧雷达：Google Play 开发者页 app 清单 diff。

iOS 雷达（itunes_releases）的 GP 补口——SLG 常 GP 先软启动（如 GAME SPARK 的
Top King 只在 GP），iOS-only 清单结构性看不见。数据源 = Google Play 公开
开发者页（免费、无鉴权、**非 Sensor Tower**，零 ST 配额）：

- 名称型 id → /store/apps/developer?id=<name>
- 数字型 id → /store/apps/dev?id=<numeric>

页面上抓 store/apps/details?id=<package> 链接得到包名清单；**仅对未见过的包**
再抓 app 详情页，从 JSON-LD（application/ld+json，GP 稳定输出）取名称/分类/
评分/价格/图标/描述。GP 无公开 API，这是最克制的页面采集：每轮 = 1 个开发者
页 + 新包数个详情页，礼貌限速 3s/请求。

diff / 基线 / 告警语义完全复用 itunes_releases.ingest_artist_apps（同一张
publisher_itunes_apps 表）：首次同步入基线不报新；GP 行 track_id=包名、
storefronts 固定 'gp'；release_date 页面拿不到记 NULL（_is_old_release 对
NULL 按"新"处理，不丢信号）。
"""
import asyncio
import json
import logging
import re
from typing import Optional
from urllib.parse import quote_plus

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.publisher import PublisherItunesArtist, PublisherItunesApp
from app.services.itunes_releases import _SF_SEEN_KEY, ingest_artist_apps

logger = logging.getLogger(__name__)

GP_BASE = "https://play.google.com"
_POLITE_DELAY_S = 3.0
# GP 对无 UA 请求行为不稳定；带常规浏览器 UA（与 hl/gl=us 固定口径配套）。
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}
_PKG_RE = re.compile(r"store/apps/details\?id=([a-zA-Z][a-zA-Z0-9_.]+)")
_JSONLD_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_OG_IMAGE_RE = re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"')


def developer_page_url(dev_id: str) -> str:
    """数字型 id 走 /dev，名称型走 /developer（GP 两套 URL 形态）。"""
    path = "dev" if dev_id.isdigit() else "developer"
    return f"{GP_BASE}/store/apps/{path}?id={quote_plus(dev_id)}"


def app_page_url(package: str) -> str:
    return f"{GP_BASE}/store/apps/details?id={package}"


async def _get_html(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, params={"hl": "en_US", "gl": "us"},
                            headers=_HEADERS, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def parse_developer_packages(html: str) -> list[str]:
    """开发者页 → 该账号下全部包名（去重保序）。"""
    out: list[str] = []
    for pkg in _PKG_RE.findall(html):
        if pkg not in out:
            out.append(pkg)
    return out


def _gp_genre(category: Optional[str]) -> Optional[str]:
    """'GAME_STRATEGY' → 'Strategy'（与 iOS 侧子品类口径对齐，便于分级 SLG）。"""
    if not category:
        return None
    return category.removeprefix("GAME_").replace("_", " ").title()


def parse_app_detail(html: str, package: str) -> dict:
    """app 详情页 → ingest_artist_apps 可直接消化的伪 iTunes 记录。

    依赖 JSON-LD（GP 稳定输出的结构化数据），布局类字段一概不碰；解析失败
    降级为仅包名记录（清单 diff 不丢，详情下轮再补不了就算了——包名即身份）。
    """
    record: dict = {
        "wrapperType": "software",
        "trackId": package,
        "trackName": package,
        "bundleId": package,
        "trackViewUrl": app_page_url(package),
        _SF_SEEN_KEY: {"gp"},
    }
    m = _JSONLD_RE.search(html)
    if m:
        try:
            ld = json.loads(m.group(1))
            record["trackName"] = (ld.get("name") or package)
            record["description"] = ld.get("description")
            record["genres"] = [g for g in [_gp_genre(ld.get("applicationCategory"))] if g]
            rating = ld.get("aggregateRating") or {}
            if rating.get("ratingValue") is not None:
                record["averageUserRating"] = float(rating["ratingValue"])
            if rating.get("ratingCount") is not None:
                record["userRatingCount"] = int(rating["ratingCount"])
            offers = ld.get("offers") or []
            if offers:
                price = str(offers[0].get("price", ""))
                record["formattedPrice"] = "Free" if price in ("0", "0.0", "") else price
            img = ld.get("image")
            if isinstance(img, str):
                record["artworkUrl512"] = img
        except (ValueError, TypeError, KeyError):
            logger.warning("gp JSON-LD parse failed for %s", package)
    if "artworkUrl512" not in record:
        og = _OG_IMAGE_RE.search(html)
        if og:
            record["artworkUrl512"] = og.group(1)
    return record


async def fetch_gp_records(dev_id: str, known_packages: set[str]) -> list[dict]:
    """拉某 GP 开发者账号的清单；只对未见过的包抓详情页（克制采集）。

    已知包回传仅含 trackId 的轻记录——ingest 对已存在行只做 storefront 并集
    （'gp' 固定不变），不会用轻记录覆盖任何字段。
    """
    async with httpx.AsyncClient(timeout=20) as client:
        html = await _get_html(client, developer_page_url(dev_id))
        packages = parse_developer_packages(html)
        records: list[dict] = []
        for pkg in packages:
            if pkg in known_packages:
                records.append({"wrapperType": "software", "trackId": pkg,
                                _SF_SEEN_KEY: {"gp"}})
                continue
            await asyncio.sleep(_POLITE_DELAY_S)
            try:
                detail_html = await _get_html(client, app_page_url(pkg))
                records.append(parse_app_detail(detail_html, pkg))
            except Exception:
                # 详情页失败不丢清单信号：降级为仅包名记录。
                logger.warning("gp app detail fetch failed for %s", pkg, exc_info=True)
                records.append({"wrapperType": "software", "trackId": pkg,
                                "trackName": pkg, "bundleId": pkg,
                                "trackViewUrl": app_page_url(pkg),
                                _SF_SEEN_KEY: {"gp"}})
    return records


async def sync_gp_releases() -> dict:
    """对全部 platform='gp' 账号跑一轮清单 diff。

    返回 {gp_synced, gp_failed, gp_baselined, gp_new_apps}。mock 模式不出外网。
    钉钉告警复用 alert_appstore_releases（按 first_seen_at 窗口读同一张表，
    与 iOS 轮各自独立窗口，不重报）。
    """
    summary = {"gp_synced": 0, "gp_failed": 0, "gp_baselined": 0, "gp_new_apps": 0}
    if settings.USE_MOCK_DATA:
        logger.info("gp releases sync skipped (mock mode)")
        return summary

    async with AsyncSessionLocal() as db:
        accounts = (await db.execute(
            select(PublisherItunesArtist).where(PublisherItunesArtist.platform == "gp")
        )).scalars().all()
    if not accounts:
        return summary

    started_at = utcnow_naive()
    for i, account in enumerate(accounts):
        if i > 0:
            await asyncio.sleep(_POLITE_DELAY_S)
        async with AsyncSessionLocal() as db:
            known = {tid for (tid,) in (await db.execute(
                select(PublisherItunesApp.track_id).where(
                    PublisherItunesApp.artist_row_id == account.id)
            )).all()}
        try:
            records = await fetch_gp_records(account.artist_id, known)
        except Exception:
            summary["gp_failed"] += 1
            logger.warning("gp developer page fetch failed for %s (%s)",
                           account.artist_id, account.label, exc_info=True)
            continue
        result = await ingest_artist_apps(account.id, records)
        summary["gp_synced"] += 1
        summary["gp_baselined"] += result["baselined"]
        summary["gp_new_apps"] += result["new_apps"]

    logger.info("gp releases sync done: %s", summary)
    if summary["gp_new_apps"] > 0:
        from app.services.release_alerts import alert_appstore_releases
        try:
            await alert_appstore_releases(since=started_at)
        except Exception:
            logger.exception("GP releases DingTalk alert failed (sync itself succeeded)")
    return summary
