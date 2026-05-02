import httpx
import random
from datetime import datetime, timedelta
from app.config import settings

MOCK_SLG_GAMES = [
    {"app_id": "com.lilithgames.rok", "name": "Rise of Kingdoms", "publisher": "Lilith Games", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/c4/6e/2b/c46e2b1f-1c97-3b53-5b76-b9e6a3e06f44/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg", "platform": "ios", "release_date": "2018-09-17"},
    {"app_id": "com.supercell.clashofclans", "name": "Clash of Clans", "publisher": "Supercell", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/6e/b4/1e/6eb41e98-2b90-7e07-f72f-c8d13be07d65/AppIcon-0-0-1x_U007emarketing-0-0-0-10-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg", "platform": "ios", "release_date": "2012-08-02"},
    {"app_id": "com.igg.mobile.lordsmobile", "name": "Lords Mobile", "publisher": "IGG.COM", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/98/a1/b3/98a1b347-f5e9-e9f9-7e8d-8a0af0f82c8a/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg", "platform": "ios", "release_date": "2016-06-02"},
    {"app_id": "com.plarium.vikings", "name": "Vikings: War of Clans", "publisher": "Plarium Global Ltd", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/57/0b/40/570b401d-3a42-2c54-5e57-df67c37e1219/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg", "platform": "ios", "release_date": "2015-08-10"},
    {"app_id": "com.century.games.warpath", "name": "Warpath", "publisher": "Lilith Games", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/6f/5e/0c/6f5e0c31-1b5c-7e92-2e1c-ebc8e9e0dd88/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg", "platform": "ios", "release_date": "2020-10-28"},
    {"app_id": "com.topgames.worldwar", "name": "Top War: Battle Game", "publisher": "Topgames.Inc", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple221/v4/17/fc/ef/17fcef5c-cfae-b06e-e3fe-ee31e5c37eec/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg", "platform": "ios", "release_date": "2019-03-01"},
    {"app_id": "com.diandian.lastwar", "name": "Last War: Survival", "publisher": "First Fun", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple221/v4/2a/5f/4c/2a5f4c6d-8b52-0a04-0d1e-8fae1a0e8d12/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg", "platform": "ios", "release_date": "2023-07-12"},
    {"app_id": "com.machines.atwar", "name": "Whiteout Survival", "publisher": "Century Games", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/f2/6a/22/f26a22b1-d4c1-af5d-f23e-a2c53a8e4741/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-0-0-0-GLES2_U002c0-512MB-85-220-0-0.png/512x512bb.jpg", "platform": "ios", "release_date": "2022-02-17"},
]

def _mock_trend(base: float, days: int, volatility: float = 0.1) -> list[dict]:
    result = []
    value = base
    for i in range(days):
        date = (datetime.now() - timedelta(days=days - i)).strftime("%Y-%m-%d")
        value = value * (1 + random.uniform(-volatility, volatility))
        result.append({"date": date, "value": round(value, 0)})
    return result

def _mock_rankings(days: int = 30) -> list[dict]:
    result = []
    for game in MOCK_SLG_GAMES:
        base_rank = random.randint(1, 50)
        for i in range(days):
            date = (datetime.now() - timedelta(days=days - i)).strftime("%Y-%m-%d")
            rank = max(1, base_rank + random.randint(-5, 5))
            result.append({
                "app_id": game["app_id"],
                "name": game["name"],
                "date": date,
                "rank": rank,
                "downloads": round(random.uniform(5000, 80000), 0),
                "revenue": round(random.uniform(50000, 2000000), 0),
            })
    return result

class SensorTowerService:
    def __init__(self):
        self.use_mock = settings.USE_MOCK_DATA or not settings.SENSOR_TOWER_API_KEY
        self.headers = {"Authorization": f"Bearer {settings.SENSOR_TOWER_API_KEY}"} if settings.SENSOR_TOWER_API_KEY else {}

    async def get_top_slg_games(self, country: str = "US", platform: str = "ios", limit: int = 20) -> list[dict]:
        if self.use_mock:
            return MOCK_SLG_GAMES[:limit]
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.SENSOR_TOWER_BASE_URL}/v1/{platform}/category/top_apps",
                params={"category": "6014", "country": country, "limit": limit},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json().get("apps", [])

    async def get_rankings(self, app_id: str, country: str = "US", platform: str = "ios", days: int = 30) -> list[dict]:
        if self.use_mock:
            game = next((g for g in MOCK_SLG_GAMES if g["app_id"] == app_id), MOCK_SLG_GAMES[0])
            base_rank = random.randint(1, 30)
            result = []
            for i in range(days):
                date = (datetime.now() - timedelta(days=days - i)).strftime("%Y-%m-%d")
                result.append({
                    "date": date,
                    "rank": max(1, base_rank + random.randint(-3, 3)),
                })
            return result
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.SENSOR_TOWER_BASE_URL}/v1/{platform}/apps/{app_id}/rankings",
                params={"country": country, "start_date": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json().get("rankings", [])

    async def get_downloads(self, app_id: str, country: str = "WW", platform: str = "ios", days: int = 30) -> list[dict]:
        if self.use_mock:
            return _mock_trend(random.uniform(20000, 100000), days)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.SENSOR_TOWER_BASE_URL}/v1/{platform}/apps/{app_id}/downloads",
                params={"country": country, "start_date": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json().get("downloads", [])

    async def get_revenue(self, app_id: str, country: str = "WW", platform: str = "ios", days: int = 30) -> list[dict]:
        if self.use_mock:
            return _mock_trend(random.uniform(100000, 3000000), days)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.SENSOR_TOWER_BASE_URL}/v1/{platform}/apps/{app_id}/revenue",
                params={"country": country, "start_date": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json().get("revenue", [])

    async def get_all_rankings_today(self, country: str = "US", platform: str = "ios") -> list[dict]:
        if self.use_mock:
            today = datetime.now().strftime("%Y-%m-%d")
            result = []
            for i, game in enumerate(MOCK_SLG_GAMES):
                result.append({
                    "app_id": game["app_id"],
                    "name": game["name"],
                    "publisher": game["publisher"],
                    "icon_url": game["icon_url"],
                    "rank": i + 1,
                    "downloads": round(random.uniform(5000, 80000), 0),
                    "revenue": round(random.uniform(50000, 2000000), 0),
                    "date": today,
                })
            return result
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.SENSOR_TOWER_BASE_URL}/v1/{platform}/category/top_apps",
                params={"category": "6014", "country": country},
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json().get("apps", [])

sensor_tower_service = SensorTowerService()
