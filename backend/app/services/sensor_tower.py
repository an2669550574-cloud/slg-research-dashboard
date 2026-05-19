import hashlib
import logging
import httpx
import random
from datetime import datetime, timedelta
from app.config import settings
from app.cache import sensor_tower_cache
from app.services import quota
from app.services.appstore import fetch_apps_bulk, fetch_play_apps
from app.services.slg_publishers import is_slg

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


def _sales_metrics(r: dict, platform: str) -> tuple[float, float]:
    """单条 sales_report_estimates 记录 → (下载量, 收入分)。
    iOS: iu+au 下载 / ir+ar 收入(分)；Android: u 下载 / r 收入(分)。"""
    if platform == "android":
        return (r.get("u") or 0), (r.get("r") or 0)
    return ((r.get("iu") or 0) + (r.get("au") or 0)), ((r.get("ir") or 0) + (r.get("ar") or 0))


def _parse_sales(data, platform: str) -> dict:
    """把 /v1/{os}/sales_report_estimates 的扁平数组解析成
    {"downloads":[{date,value}], "revenue":[{date,value}]}（value=下载量/美元）。

    字段是缩写：iOS d/iu/au(下载) ir/ar(收入,分)，Android d/u(下载) r(收入,分)。
    fallback / mock 已是目标形状，原样透传。同日多行（多区累计）按日求和。
    """
    if isinstance(data, dict) and "downloads" in data:
        return {"downloads": data.get("downloads", []), "revenue": data.get("revenue", [])}
    rows = data if isinstance(data, list) else (data.get("data") or data.get("results") or [])
    dl: dict[str, float] = {}
    rev_cents: dict[str, float] = {}
    for r in rows:
        d = r.get("d") or r.get("date")
        if not d:
            continue
        # ST 返回的是 "2026-05-14T00:00:00Z"；本地排名序列用 "2026-05-14"。
        # 截到日，两条序列在前端图表 X 轴才对得齐。
        d = d[:10]
        u, c = _sales_metrics(r, platform)
        dl[d] = dl.get(d, 0) + u
        rev_cents[d] = rev_cents.get(d, 0) + c
    downloads = [{"date": d, "value": round(dl[d], 0)} for d in sorted(dl)]
    revenue = [{"date": d, "value": round(rev_cents[d] / 100.0, 2)} for d in sorted(rev_cents)]
    return {"downloads": downloads, "revenue": revenue}


def _parse_sales_by_app(data, platform: str) -> dict:
    """批量 sales_report_estimates → {app_id: {downloads, revenue}}，每个 app
    取其**最新一天**的估算（ST 数据通常 T-1/T-2，日榜显示最近可得值）。
    字段同 _parse_sales：iOS d/iu+au / ir+ar(分)，Android d/u / r(分)。
    fallback / 空 → {}。"""
    rows = data if isinstance(data, list) else (data.get("data") or data.get("results") or [])
    latest: dict[str, str] = {}
    out: dict[str, dict] = {}
    for r in rows:
        aid = r.get("aid") or r.get("app_id")
        d = r.get("d") or r.get("date")
        if not aid or not d:
            continue
        aid, d = str(aid), d[:10]
        if aid in latest and d <= latest[aid]:
            continue
        latest[aid] = d
        u, c = _sales_metrics(r, platform)
        out[aid] = {"downloads": round(u, 0), "revenue": round(c / 100.0, 2)}
    return out


def _parse_sales_series(data, platform: str) -> dict:
    """sales_report_estimates 扁平数组 → {app_id: {date: {"downloads","revenue"}}}。

    历史回填用：保留每个 app 的**每一天**（不像 _parse_sales_by_app 只取最新）。
    同 app 同日多区记录按日累加。收入分→美元。"""
    rows = data if isinstance(data, list) else (data.get("data") or data.get("results") or [])
    agg: dict = {}  # (aid, date) -> [downloads, revenue_cents]
    for r in rows:
        aid = r.get("aid") or r.get("app_id")
        d = r.get("d") or r.get("date")
        if not aid or not d:
            continue
        aid, d = str(aid), d[:10]
        u, c = _sales_metrics(r, platform)
        cell = agg.setdefault((aid, d), [0.0, 0.0])
        cell[0] += u
        cell[1] += c
    out: dict = {}
    for (aid, d), (u, c) in agg.items():
        out.setdefault(aid, {})[d] = {"downloads": round(u, 0), "revenue": round(c / 100.0, 2)}
    return out


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
            "is_slg": True,  # MOCK_SLG_GAMES 全是 SLG
        }
        for i, g in enumerate(MOCK_SLG_GAMES)
    ]


class SensorTowerService:
    def __init__(self):
        self.use_mock = settings.USE_MOCK_DATA or not settings.SENSOR_TOWER_API_KEY
        # Sensor Tower 鉴权是 auth_token 查询参数，不是 Authorization 头（之前用
        # Bearer 头导致所有真实调用 404、白烧配额一个月）。
        self.api_token = settings.SENSOR_TOWER_API_KEY or ""
        # mock 数据每次都是 random，不缓存（否则永远不变）。真实请求才缓存。
        self.cache_ttl = settings.SENSOR_TOWER_CACHE_TTL
        self.snapshot_fresh_seconds = settings.SENSOR_TOWER_SNAPSHOT_FRESH_HOURS * 3600

    async def _get(self, path: str, params: dict) -> dict:
        # 复制 params 再注入 auth_token，避免污染调用方 dict 与缓存 key。
        q = {**params, "auth_token": self.api_token}
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(f"{settings.SENSOR_TOWER_BASE_URL}{path}", params=q)
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

    def _window_key(self, days: int, start_date: str | None, end_date: str | None) -> str:
        if start_date and end_date:
            return f"{start_date}_{end_date}"
        return f"d{days}"

    async def get_rankings(self, app_id: str, country: str = "US", platform: str = "ios", days: int = 30, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
        # 真实排名走势改由 /games/{app_id}/metrics 路由直接查本地 game_rankings
        # 表（每日调度已采集，零 ST 配额）。这里只服务 mock 模式。
        if self.use_mock:
            return _mock_rank_series(days, start_date, end_date)
        return []

    async def get_sales(self, app_id: str, country: str = "WW", platform: str = "ios", days: int = 30, start_date: str | None = None, end_date: str | None = None) -> dict:
        """下载+收入一次取（真实接口 /v1/{os}/sales_report_estimates 同时返回
        两者）。返回 {"downloads":[{date,value}], "revenue":[{date,value}]}，
        value 为下载量 / 美元。"""
        if self.use_mock:
            return {
                "downloads": _mock_trend(random.uniform(20000, 100000), days, start_date, end_date),
                "revenue": _mock_trend(random.uniform(100000, 3000000), days, start_date, end_date),
            }
        win = _resolve_window(days, start_date, end_date)
        key = f"sales:{platform}:{country}:{app_id}:{self._window_key(days, start_date, end_date)}"
        data = await self._cached_get(
            key,
            f"/v1/{platform}/sales_report_estimates",
            {"app_ids": app_id, "countries": country,
             "date_granularity": "daily", "start_date": win[0], "end_date": win[-1]},
            fallback=lambda: {
                "downloads": _mock_trend(random.uniform(20000, 100000), days, start_date, end_date),
                "revenue": _mock_trend(random.uniform(100000, 3000000), days, start_date, end_date),
            },
        )
        return _parse_sales(data, platform)

    async def get_sales_batch(self, app_ids: list[str], country: str, platform: str, days_window: int = 7) -> dict:
        """日榜前 N 名一次取下载/收入：app_ids 逗号批量 → **单次配额**拿全部，
        每个 app 取最新一天。返回 {app_id: {downloads, revenue}}；失败/空 → {}。
        cache key 用排序后 ids 的短 hash + 当日，按天自然轮换、不撑爆 255 长度。"""
        if self.use_mock or not app_ids:
            return {}
        win = _resolve_window(days_window, None, None)
        h = hashlib.md5(",".join(sorted(app_ids)).encode()).hexdigest()[:12]
        key = f"salesbatch:{platform}:{country}:{win[-1]}:{h}"
        data = await self._cached_get(
            key,
            f"/v1/{platform}/sales_report_estimates",
            {"app_ids": ",".join(app_ids), "countries": country,
             "date_granularity": "daily", "start_date": win[0], "end_date": win[-1]},
            fallback=lambda: {},
        )
        return _parse_sales_by_app(data, platform)

    async def fetch_sales_series(self, app_ids: list[str], country: str, platform: str,
                                 start_date: str, end_date: str):
        """历史回填底层：单次 sales_report_estimates（日粒度、批量 app_ids、
        指定区间），按月度配额计费（不走 L1/L2，不污染缓存键）。

        返回 {app_id: {date: {downloads, revenue}}}；配额耗尽返回 None（调用方
        应停止整轮回填）；本次调用失败返回 {}（已退还配额，调用方可继续下一段）。
        失败/退还纪律对齐 _cached_get。"""
        if self.use_mock or not app_ids:
            return {}
        if not await quota.try_consume():
            logger.warning("Sales backfill aborted: monthly quota exhausted")
            return None
        params = {"app_ids": ",".join(app_ids), "countries": country,
                  "date_granularity": "daily", "start_date": start_date, "end_date": end_date}
        try:
            data = await self._get(f"/v1/{platform}/sales_report_estimates", params)
        except Exception as e:
            await quota.refund()
            logger.error("Sales backfill chunk failed %s/%s %s..%s: %s",
                         country, platform, start_date, end_date, e)
            return {}
        return _parse_sales_series(data, platform)

    def _today_key(self, country: str, platform: str) -> tuple[str, str, str]:
        """今日榜的 (cache_key, chart_type, category)。key 必须含 chart_type+
        category：否则切免费/畅销榜或换类目后旧快照会被继续返回（最长 24h）。
        get_all_rankings_today 与 force_refresh 共用，保证失效命中同一 key。"""
        if platform == "android":
            category = settings.SENSOR_TOWER_RANKING_CATEGORY_ANDROID
            chart_type = settings.SENSOR_TOWER_RANKING_CHART_TYPE_ANDROID
        else:
            category = settings.SENSOR_TOWER_RANKING_CATEGORY_IOS
            chart_type = settings.SENSOR_TOWER_RANKING_CHART_TYPE_IOS
        return f"today:{platform}:{country}:{chart_type}:{category}", chart_type, category

    async def get_all_rankings_today(self, country: str = "US", platform: str = "ios") -> list[dict]:
        if self.use_mock:
            return _mock_today_rankings()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        key, chart_type, category = self._today_key(country, platform)
        data = await self._cached_get(
            key,
            f"/v1/{platform}/ranking",
            {
                "category": category,
                "country": country,
                "chart_type": chart_type,
                "date": today,
                "limit": settings.SENSOR_TOWER_RANKING_LIMIT,
            },
            fallback=lambda: {"apps": _mock_today_rankings()},
        )
        # /v1/{os}/ranking 只返回有序 app_id 列表（无名字/下载/收入）。名次+
        # app_id 来自 ST；名字/出版商/图标免费补全：iOS 走 iTunes 批量，Android
        # 走 Google Play 商品页（iTunes 没安卓）。下载/收入仍留空（省配额，详情
        # 页另取）。mock 兜底走旧 apps 形状。
        if "ranking" in data:
            rows = [
                {"app_id": str(aid), "rank": i + 1, "name": None, "publisher": None,
                 "icon_url": None, "downloads": None, "revenue": None, "date": today,
                 "is_slg": False}
                for i, aid in enumerate(data.get("ranking") or [])
            ]
            ids = [r["app_id"] for r in rows]
            if platform == "android":
                meta = await fetch_play_apps(
                    ids, country=country.lower(),
                    max_apps=settings.SENSOR_TOWER_ANDROID_ENRICH_LIMIT)
            else:
                meta = await fetch_apps_bulk(ids, country=country.lower())
            for r in rows:
                m = meta.get(r["app_id"])
                if m:
                    r["name"], r["publisher"], r["icon_url"] = m["name"], m["publisher"], m["icon_url"]
                r["is_slg"] = is_slg(r["app_id"], r["publisher"])
            # 前 N 名补真实下载/收入（一次批量调用，+1 配额）。榜尾保持 None
            # → 前端显示"—"，区分"无数据"与真实 0。
            topn = settings.SENSOR_TOWER_RANKING_SALES_TOPN
            if topn > 0:
                top_ids = [r["app_id"] for r in rows[:topn]]
                sales = await self.get_sales_batch(top_ids, country, platform)
                for r in rows:
                    s = sales.get(r["app_id"])
                    if s:
                        r["downloads"], r["revenue"] = s["downloads"], s["revenue"]
            return rows
        return data.get("apps", [])

    async def get_ranking_on_date(
        self, country: str, platform: str, date: str
    ) -> list[dict]:
        """某历史日某 (国家,平台) 品类榜 → [{"app_id","rank"}]（仅名次，
        不补名字/不取销量——历史排名回填只要名次，省配额省时）。

        走 _cached_get：配额计入月度预算 + 落 L2 快照（重跑/重启不重烧）。
        cache_key 含 date+chart_type+category，与今日榜 key 隔离、互不串。
        """
        if self.use_mock:
            return [{"app_id": r["app_id"], "rank": r["rank"]}
                    for r in _mock_today_rankings()]
        _, chart_type, category = self._today_key(country, platform)
        key = f"rankhist:{platform}:{country}:{chart_type}:{category}:{date}"
        data = await self._cached_get(
            key,
            f"/v1/{platform}/ranking",
            {
                "category": category,
                "country": country,
                "chart_type": chart_type,
                "date": date,
                "limit": settings.RANK_BACKFILL_LIMIT,
            },
            fallback=lambda: {"ranking": []},
        )
        return [{"app_id": str(aid), "rank": i + 1}
                for i, aid in enumerate(data.get("ranking") or [])]

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
        key, _, _ = self._today_key(country, platform)
        await sensor_tower_cache.invalidate(key)
        await quota.delete_snapshot(key)
        # 清同 country/platform 的 metrics L1，让下次读穿透到 L2 snapshot
        metric_suffix = f":{platform}:{country}:"
        await sensor_tower_cache.invalidate_matching(
            lambda k: k.startswith(("rank", "dl:", "rev:")) and metric_suffix in k
        )
        return await self.get_all_rankings_today(country, platform)


sensor_tower_service = SensorTowerService()
