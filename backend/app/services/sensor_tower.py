import logging
import httpx
import random
from datetime import datetime, timedelta
from app.config import settings
from app.cache import sensor_tower_cache
from app.services import quota

logger = logging.getLogger(__name__)

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

HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def _date_range(start: datetime, end: datetime) -> list[str]:
    days = max(1, (end.date() - start.date()).days + 1)
    return [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]


def _resolve_window(days: int, start_date: str | None, end_date: str | None) -> list[str]:
    """根据 days 或显式 start/end 返回日期序列（升序）。"""
    if start_date and end_date:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        if end < start:
            start, end = end, start
        return _date_range(start, end)
    end = datetime.now()
    start = end - timedelta(days=days - 1)
    return _date_range(start, end)


def _mock_trend(base: float, days: int, start_date: str | None = None, end_date: str | None = None, volatility: float = 0.1) -> list[dict]:
    dates = _resolve_window(days, start_date, end_date)
    value = base
    result = []
    for d in dates:
        value = value * (1 + random.uniform(-volatility, volatility))
        result.append({"date": d, "value": round(value, 0)})
    return result


def _mock_rank_series(days: int, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    dates = _resolve_window(days, start_date, end_date)
    base_rank = random.randint(1, 30)
    return [{"date": d, "rank": max(1, base_rank + random.randint(-3, 3))} for d in dates]


def _mock_today_rankings() -> list[dict]:
    today = datetime.now().strftime("%Y-%m-%d")
    return [
        {
            "app_id": g["app_id"],
            "name": g["name"],
            "publisher": g["publisher"],
            "icon_url": g["icon_url"],
            "rank": i + 1,
            "downloads": round(random.uniform(5000, 80000), 0),
            "revenue": round(random.uniform(50000, 2000000), 0),
            "date": today,
        }
        for i, g in enumerate(MOCK_SLG_GAMES)
    ]


class SensorTowerService:
    def __init__(self):
        self.use_mock = settings.USE_MOCK_DATA or not settings.SENSOR_TOWER_API_KEY
        self.headers = {"Authorization": f"Bearer {settings.SENSOR_TOWER_API_KEY}"} if settings.SENSOR_TOWER_API_KEY else {}
        # mock 数据每次都是 random，不缓存（否则永远不变）。真实请求才缓存。
        self.cache_ttl = settings.SENSOR_TOWER_CACHE_TTL

    async def _get(self, path: str, params: dict) -> dict:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(
                f"{settings.SENSOR_TOWER_BASE_URL}{path}",
                params=params,
                headers=self.headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def _cached_get(self, cache_key: str, path: str, params: dict, fallback) -> dict:
        """真实请求走缓存 + single-flight；月度配额超限时降级到持久化快照；
        快照也没有 / 网络失败时再降级到 fallback()。"""
        async def loader():
            allowed = await quota.try_consume()
            if not allowed:
                snapshot = await quota.load_snapshot(cache_key)
                if snapshot is not None:
                    logger.info("Sensor Tower quota exhausted, serving snapshot for %s", cache_key)
                    return snapshot
                logger.warning("Sensor Tower quota exhausted and no snapshot for %s, using mock", cache_key)
                return fallback()
            data = await self._get(path, params)
            # 真实调用成功 → 持久化一份"最后已知好数据"，月底超额时回读
            try:
                await quota.save_snapshot(cache_key, data)
            except Exception as e:
                logger.warning("Failed to save snapshot for %s: %s", cache_key, e)
            return data
        try:
            return await sensor_tower_cache.get_or_set(cache_key, self.cache_ttl, loader)
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Sensor Tower fetch failed (%s), falling back: %s", cache_key, e)
            # 网络失败时也尝试快照
            snapshot = await quota.load_snapshot(cache_key)
            if snapshot is not None:
                return snapshot
            return fallback()

    async def get_top_slg_games(self, country: str = "US", platform: str = "ios", limit: int = 20) -> list[dict]:
        if self.use_mock:
            return MOCK_SLG_GAMES[:limit]
        key = f"top:{platform}:{country}:{limit}"
        data = await self._cached_get(
            key,
            f"/v1/{platform}/category/top_apps",
            {"category": "6014", "country": country, "limit": limit},
            fallback=lambda: {"apps": MOCK_SLG_GAMES[:limit]},
        )
        return data.get("apps", [])

    def _real_window(self, days: int, start_date: str | None, end_date: str | None) -> dict:
        if start_date and end_date:
            return {"start_date": start_date, "end_date": end_date}
        return {"start_date": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")}

    def _window_key(self, days: int, start_date: str | None, end_date: str | None) -> str:
        if start_date and end_date:
            return f"{start_date}_{end_date}"
        return f"d{days}"

    async def get_rankings(self, app_id: str, country: str = "US", platform: str = "ios", days: int = 30, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
        if self.use_mock:
            return _mock_rank_series(days, start_date, end_date)
        key = f"rank:{platform}:{country}:{app_id}:{self._window_key(days, start_date, end_date)}"
        data = await self._cached_get(
            key,
            f"/v1/{platform}/apps/{app_id}/rankings",
            {"country": country, **self._real_window(days, start_date, end_date)},
            fallback=lambda: {"rankings": _mock_rank_series(days, start_date, end_date)},
        )
        return data.get("rankings", [])

    async def get_downloads(self, app_id: str, country: str = "WW", platform: str = "ios", days: int = 30, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
        if self.use_mock:
            return _mock_trend(random.uniform(20000, 100000), days, start_date, end_date)
        key = f"dl:{platform}:{country}:{app_id}:{self._window_key(days, start_date, end_date)}"
        data = await self._cached_get(
            key,
            f"/v1/{platform}/apps/{app_id}/downloads",
            {"country": country, **self._real_window(days, start_date, end_date)},
            fallback=lambda: {"downloads": _mock_trend(random.uniform(20000, 100000), days, start_date, end_date)},
        )
        return data.get("downloads", [])

    async def get_revenue(self, app_id: str, country: str = "WW", platform: str = "ios", days: int = 30, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
        if self.use_mock:
            return _mock_trend(random.uniform(100000, 3000000), days, start_date, end_date)
        key = f"rev:{platform}:{country}:{app_id}:{self._window_key(days, start_date, end_date)}"
        data = await self._cached_get(
            key,
            f"/v1/{platform}/apps/{app_id}/revenue",
            {"country": country, **self._real_window(days, start_date, end_date)},
            fallback=lambda: {"revenue": _mock_trend(random.uniform(100000, 3000000), days, start_date, end_date)},
        )
        return data.get("revenue", [])

    async def get_all_rankings_today(self, country: str = "US", platform: str = "ios") -> list[dict]:
        if self.use_mock:
            return _mock_today_rankings()
        key = f"today:{platform}:{country}"
        data = await self._cached_get(
            key,
            f"/v1/{platform}/category/top_apps",
            {"category": "6014", "country": country},
            fallback=lambda: {"apps": _mock_today_rankings()},
        )
        return data.get("apps", [])


sensor_tower_service = SensorTowerService()
