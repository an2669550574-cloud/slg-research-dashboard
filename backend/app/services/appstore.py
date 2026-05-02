import httpx

async def fetch_app_info(app_id: str, country: str = "us") -> dict | None:
    """iTunes Search API — 免费公开接口，获取 App 基本信息和版本历史"""
    # 尝试用数字 ID 查询
    numeric_id = app_id.replace("id", "").strip()
    if not numeric_id.isdigit():
        return None

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://itunes.apple.com/lookup",
            params={"id": numeric_id, "country": country, "entity": "software"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data.get("results"):
            return None
        app = data["results"][0]
        return {
            "name": app.get("trackName"),
            "publisher": app.get("artistName"),
            "icon_url": app.get("artworkUrl512") or app.get("artworkUrl100"),
            "release_date": app.get("releaseDate", "")[:10],
            "description": app.get("description", "")[:500],
            "version": app.get("version"),
            "release_notes": app.get("releaseNotes", ""),
            "genres": app.get("genres", []),
        }
