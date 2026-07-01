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
storefronts 固定 'gp'；release_date 页面拿不到记 NULL。**GP 老品门控靠评价数**：
release_date 缺失时 ingest 退回 _is_established(rating_count) 判老（GP 开发者页
分页、首同步漏抓的老游戏下轮现身，评价数一眼是老品——如 EasyTech 的
World Conqueror 2），评价数缺失/低仍按"新"处理不丢信号。
"""
import asyncio
import json
import logging
import re
from html import unescape
from typing import Optional
from urllib.parse import quote_plus, unquote_plus

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
# 「关于这款游戏」完整正文容器：GP 稳定输出 data-g-id="description"。JSON-LD 的
# description 往往只是标题下的短标语，正文才是真简介——能解析就用更长的那个。
_DESC_BLOCK_RE = re.compile(r'data-g-id="description"[^>]*>(.*?)</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _full_description(html: str) -> Optional[str]:
    """从详情页抽「关于这款游戏」完整正文（best-effort，失败返回 None 回退 JSON-LD）。"""
    m = _DESC_BLOCK_RE.search(html)
    if not m:
        return None
    txt = re.sub(r"<br\s*/?>", "\n", m.group(1))
    txt = unescape(_TAG_RE.sub("", txt)).strip()
    return txt or None


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


# app 详情页里的开发者链接（数字型 /dev?id= 或名称型 /developer?id=）。
_DEV_LINK_RE = re.compile(r"/store/apps/(?:dev|developer)\?id=([^\"&\\]+)")
# 开发者页 og:title 形如 "Android Apps by X on Google Play" → 取 X。
_DEV_OGTITLE_RE = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"')


def _parse_gp_developer_name(html: str) -> Optional[str]:
    m = _DEV_OGTITLE_RE.search(html)
    if not m:
        return None
    t = unescape(m.group(1)).strip()
    t = re.sub(r"^Android Apps by\s+", "", t)
    t = re.sub(r"\s+on Google Play$", "", t)
    return t.strip() or None


async def resolve_gp_developer_for_package(package: str) -> Optional[dict]:
    """反向解析：GP app 详情页 → 该 app 的开发者账号 (dev_id, dev_name)。

    「雷达覆盖建议」GP 侧用——主体钉了安卓包名 / 在榜有安卓产品但还没接 GP 雷达时，
    从包名免费反解出 GP 开发者 id 供一键接入（与 itunes_releases.resolve_artist_for_app
    iOS 侧对称）。返回 {"artist_id": dev_id, "artist_name": dev_name|None, "app_name": str|None}
    （键名与 iOS 反解**同形**，便于「雷达覆盖建议」端点统一处理两侧）。仅安卓包名（含 `.`、
    非纯数字）有效。免费页面采集、零 ST。失败 / 页面无开发者链接 → None。
    """
    if not package or "." not in package or package.isdigit():
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            html = await _get_html(client, app_page_url(package))
            ids = [unquote_plus(x) for x in _DEV_LINK_RE.findall(html)]
            if not ids:
                return None
            # 该 app 的开发者链接在页内多次出现；取出现最多的（排除「相似应用」其它开发者）。
            dev_id = max(set(ids), key=ids.count)
            app_name = parse_app_detail(html, package).get("trackName")
            # 开发者名 best-effort：再抓一次开发者页拿 og:title（供人工核对账号归属，失败不影响）。
            dev_name = None
            try:
                dev_name = _parse_gp_developer_name(await _get_html(client, developer_page_url(dev_id)))
            except Exception:
                logger.warning("gp developer name fetch failed for dev %s", dev_id)
    except Exception:
        logger.warning("gp developer resolve failed for %s", package, exc_info=True)
        return None
    return {"artist_id": dev_id, "artist_name": dev_name, "app_name": app_name}


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
            # JSON-LD description 常是短标语；正文容器能解析出更长的就用正文（回退短的）。
            ld_desc = (ld.get("description") or "").strip()
            full_desc = _full_description(html) or ""
            record["description"] = full_desc if len(full_desc) > len(ld_desc) else (ld_desc or None)
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
            shots = ld.get("screenshot")
            if isinstance(shots, str):
                shots = [shots]
            if isinstance(shots, list):
                record["screenshotUrls"] = [
                    u for u in shots if isinstance(u, str) and u.startswith("http")][:5]
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
