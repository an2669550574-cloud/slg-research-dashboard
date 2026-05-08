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
