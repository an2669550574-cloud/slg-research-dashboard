import asyncio
import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

_PLAY_LD_RE = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL)


async def fetch_app_info(app_id: str, country: str = "us") -> dict | None:
    """iTunes Search API — 免费公开接口，获取 App 基本信息和版本历史。

    任何网络/解析失败都返回 None，让调用方走降级路径。
    """
    numeric_id = app_id.replace("id", "").strip()
    if not numeric_id.isdigit():
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://itunes.apple.com/lookup",
                params={"id": numeric_id, "country": country, "entity": "software"},
            )
            if resp.status_code != 200:
                logger.warning("iTunes lookup status %s for %s", resp.status_code, app_id)
                return None
            data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("iTunes lookup failed for %s: %s", app_id, e)
        return None

    if not data.get("results"):
        return None

    app = data["results"][0]
    return {
        "name": app.get("trackName"),
        "publisher": app.get("artistName"),
        "icon_url": app.get("artworkUrl512") or app.get("artworkUrl100"),
        "release_date": (app.get("releaseDate") or "")[:10],
        "current_version_date": (app.get("currentVersionReleaseDate") or "")[:10],
        "description": (app.get("description") or "")[:500],
        "version": app.get("version"),
        "release_notes": app.get("releaseNotes", ""),
        "genres": app.get("genres", []),
    }


async def fetch_apps_bulk(app_ids: list[str], country: str = "us") -> dict[str, dict]:
    """批量 iTunes lookup（id 逗号拼接，分块）。仅 iOS 数字 id；Android 包名
    非数字会被过滤掉。返回 {app_id: {name, publisher, icon_url, version,
    current_version_date, release_notes}}。

    榜单补全用：500 个 app 一条条查太慢，iTunes lookup 支持一次多 id。
    version_tracker 复用同一批量查拿版本号（同响应自带，零额外请求）。
    任何网络/解析失败按块静默降级，调用方对缺失项保持原值（None）。
    """
    ids = [a for a in dict.fromkeys(str(x) for x in app_ids) if a.isdigit()]
    if not ids:
        return {}
    out: dict[str, dict] = {}
    CHUNK = 100
    async with httpx.AsyncClient(timeout=15) as client:
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i:i + CHUNK]
            try:
                resp = await client.get(
                    "https://itunes.apple.com/lookup",
                    params={"id": ",".join(chunk), "country": country, "entity": "software"},
                )
                if resp.status_code != 200:
                    logger.warning("iTunes bulk lookup status %s (%d ids)", resp.status_code, len(chunk))
                    continue
                results = resp.json().get("results", [])
            except (httpx.HTTPError, ValueError) as e:
                logger.warning("iTunes bulk lookup failed (%d ids): %s", len(chunk), e)
                continue
            for app in results:
                tid = app.get("trackId")
                if tid is None:
                    continue
                out[str(tid)] = {
                    "name": app.get("trackName"),
                    "publisher": app.get("artistName"),
                    "icon_url": app.get("artworkUrl512") or app.get("artworkUrl100"),
                    "version": app.get("version"),
                    "current_version_date": (app.get("currentVersionReleaseDate") or "")[:10] or None,
                    "release_notes": app.get("releaseNotes") or None,
                }
    return out


async def fetch_play_apps(pkg_ids: list[str], country: str = "us",
                          max_apps: int = 60, concurrency: int = 8) -> dict[str, dict]:
    """Google Play 商品页抓 name/publisher/icon（无官方 API，解析页面里的
    application/ld+json）。Android 包名 iTunes 查不到，只能走这条。

    只取前 max_apps 个：榜尾对竞品监控无意义，且 Play 无批量接口、一个包名
    一次请求，限量同时压低耗时与封 IP 风险。并发用信号量收口。任何失败按
    app 静默降级 → 调用方保持 None（前端 GameIcon 字母兜底）。
    """
    pkgs = [p for p in dict.fromkeys(pkg_ids) if p and not p.isdigit()][:max_apps]
    if not pkgs:
        return {}
    out: dict[str, dict] = {}
    sem = asyncio.Semaphore(concurrency)

    async def one(client: httpx.AsyncClient, pkg: str):
        async with sem:
            try:
                r = await client.get(
                    "https://play.google.com/store/apps/details",
                    params={"id": pkg, "hl": "en", "gl": country},
                )
                if r.status_code != 200:
                    return
                blocks = _PLAY_LD_RE.findall(r.text)
            except (httpx.HTTPError, ValueError) as e:
                logger.warning("Play fetch failed for %s: %s", pkg, e)
                return
            for b in blocks:
                try:
                    ld = json.loads(b)
                except ValueError:
                    continue
                if not isinstance(ld, dict) or ld.get("@type") != "SoftwareApplication":
                    continue
                author = ld.get("author")
                pub = (author.get("name") if isinstance(author, dict)
                       else author if isinstance(author, str) else None)
                out[pkg] = {
                    "name": ld.get("name"),
                    "publisher": pub,
                    "icon_url": ld.get("image"),
                }
                return

    async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0"}) as client:
        await asyncio.gather(*(one(client, p) for p in pkgs))
    return out
