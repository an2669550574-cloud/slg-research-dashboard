import logging
import httpx
import random
from datetime import datetime, timedelta
from app.config import settings
from app.cache import sensor_tower_cache
from app.services import quota

logger = logging.getLogger(__name__)

MOCK_SLG_GAMES = [
    {"app_id": "com.lilithgames.rok", "name": "Rise of Kingdoms", "publisher": "Lilith Games", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple221/v4/0e/8f/db/0e8fdba6-070e-ecd7-d640-030cdb279688/AppIcon-0-0-1x_U007emarketing-0-8-0-85-220.png/512x512bb.jpg", "platform": "ios", "release_date": "2018-09-17"},
    {"app_id": "com.supercell.clashofclans", "name": "Clash of Clans", "publisher": "Supercell", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/11/c2/6f/11c26fc3-be04-9f1b-379b-5a233ff299b8/AppIcon-0-0-1x_U007emarketing-0-8-0-85-220.png/512x512bb.jpg", "platform": "ios", "release_date": "2012-08-02"},
    {"app_id": "com.igg.mobile.lordsmobile", "name": "Lords Mobile", "publisher": "IGG.COM", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/eb/10/39/eb103945-9a7d-fc7d-57b3-9a10f5c5552f/AppIcon-1x_U007emarketing-0-8-0-85-220-0.png/512x512bb.jpg", "platform": "ios", "release_date": "2016-06-02"},
    {"app_id": "com.plarium.vikings", "name": "Vikings: War of Clans", "publisher": "Plarium Global Ltd", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/43/7f/1f/437f1fdc-6918-e57a-eb92-a5e231bad77d/AppIcon-0-0-1x_U007emarketing-0-8-0-85-220.png/512x512bb.jpg", "platform": "ios", "release_date": "2015-08-10"},
    {"app_id": "com.century.games.warpath", "name": "Warpath", "publisher": "Lilith Games", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/13/ea/f1/13eaf132-1d15-e47a-8fac-c26540bb25f7/AppIcon-0-0-1x_U007emarketing-0-11-0-85-220.png/512x512bb.jpg", "platform": "ios", "release_date": "2020-10-28"},
    {"app_id": "com.topgames.worldwar", "name": "Top War: Battle Game", "publisher": "Topgames.Inc", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple221/v4/24/ce/d3/24ced314-2713-f292-78fb-c2b5480b0eca/AppIcon-gl-1-0-0-1x_U007emarketing-0-11-0-0-85-220.png/512x512bb.jpg", "platform": "ios", "release_date": "2019-03-01"},
    {"app_id": "com.diandian.lastwar", "name": "Last War: Survival", "publisher": "First Fun", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/43/bb/21/43bb21ce-1039-f722-aa4b-b4fd93fb52c5/AppIcon-0-0-1x_U007emarketing-0-8-0-85-220.png/512x512bb.jpg", "platform": "ios", "release_date": "2023-07-12"},
    {"app_id": "com.machines.atwar", "name": "Whiteout Survival", "publisher": "Century Games", "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple221/v4/e5/1e/1d/e51e1db7-4655-6bb8-9441-af76dbfd291c/AppIcon-0-0-1x_U007emarketing-0-8-0-85-220.png/512x512bb.jpg", "platform": "ios", "release_date": "2022-02-17"},
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
        self.snapshot_fresh_seconds = settings.SENSOR_TOWER_SNAPSHOT_FRESH_HOURS * 3600

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
        """缓存层级：
            L1: 进程内 InMemoryTTLCache（cache_ttl 秒）
            L2: SQLite sensor_tower_snapshots（snapshot_fresh_seconds 秒）
            真实 API 仅在 L1+L2 都 miss 且配额允许时调用。

        月度配额仅在真正打 Sensor Tower 时消耗。L2 命中等于免费一天。
        """
        async def loader():
            # L2: SQLite 里若有日级新鲜快照，直接返回，不消耗配额
            fresh = await quota.load_snapshot_if_fresh(cache_key, self.snapshot_fresh_seconds)
            if fresh is not None:
                logger.debug("Sensor Tower snapshot-fresh hit for %s", cache_key)
                return fresh

            # L2 也 miss → 占用月度配额
            allowed = await quota.try_consume()
            if not allowed:
                # 配额耗尽 → 拿任何快照（即使过期）作为最后已知好数据
                stale = await quota.load_snapshot(cache_key)
                if stale is not None:
                    logger.info("Sensor Tower quota exhausted, serving stale snapshot for %s", cache_key)
                    return stale
                logger.warning("Sensor Tower quota exhausted and no snapshot for %s, using mock", cache_key)
                return fallback()

            try:
                data = await self._get(path, params)
            except Exception:
                # 失败不该扣配额：try_consume 已扣，退还再抛给外层降级处理。
                await quota.refund()
                raise
            # 真实调用成功 → 持久化一份"最后已知好数据"
            try:
                await quota.save_snapshot(cache_key, data)
            except Exception as e:
                logger.error("Failed to save snapshot for %s: %s", cache_key, e)
            return data
        try:
            return await sensor_tower_cache.get_or_set(cache_key, self.cache_ttl, loader)
        except (httpx.HTTPError, ValueError) as e:
            # error 级（非 warning）→ 经 LoggingIntegration 进 Sentry。这正是
            # 之前端点 404 静默烧配额一个月没人发现的那条信号。
            logger.error("Sensor Tower fetch failed (%s), falling back: %s", cache_key, e)
            # 网络失败时也尝试任何快照
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

    async def force_refresh_today_rankings(self, country: str = "US", platform: str = "ios") -> list[dict]:
        """绕过 L1+L2 缓存，强制重新拉取今日榜单。会消耗一次月度配额。

        典型用例：dashboard 上的"刷新数据"按钮——用户明确想看最新数据。
        清完两层缓存后再调 _cached_get，必然 miss → 走真实 API → 写新 snapshot。

        副作用：同 country/platform 的 metrics L1 缓存也被清空（rank/dl/rev），
        让下次进游戏详情时能从 L2 snapshot 重读到与新今日数据同源的曲线。
        L2 snapshot 不动——避免误清后下次读取又得消耗 N×3 配额重建。
        """
        if self.use_mock:
            return _mock_today_rankings()
        key = f"today:{platform}:{country}"
        await sensor_tower_cache.invalidate(key)
        await quota.delete_snapshot(key)
        # 清同 country/platform 的 metrics L1，让下次读穿透到 L2 snapshot
        metric_suffix = f":{platform}:{country}:"
        await sensor_tower_cache.invalidate_matching(
            lambda k: k.startswith(("rank", "dl:", "rev:")) and metric_suffix in k
        )
        return await self.get_all_rankings_today(country, platform)


sensor_tower_service = SensorTowerService()
