import logging
import httpx

logger = logging.getLogger(__name__)


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
        "description": (app.get("description") or "")[:500],
        "version": app.get("version"),
        "release_notes": app.get("releaseNotes", ""),
        "genres": app.get("genres", []),
    }


async def fetch_apps_bulk(app_ids: list[str], country: str = "us") -> dict[str, dict]:
    """批量 iTunes lookup（id 逗号拼接，分块）。仅 iOS 数字 id；Android 包名
    非数字会被过滤掉。返回 {app_id: {name, publisher, icon_url}}。

    榜单补全用：500 个 app 一条条查太慢，iTunes lookup 支持一次多 id。
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
                }
    return out
