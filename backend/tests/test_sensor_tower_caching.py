"""验证 SensorTowerService._cached_get 的两层缓存策略。

关注点：snapshot-first 路径在快照新鲜时跳过真实 API 且不消耗月度配额。
"""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_cached_get_uses_fresh_snapshot_without_consuming_quota(client):
    """L1 miss + L2 fresh hit → 不调 httpx、不消耗配额。"""
    from app.services import quota
    from app.services.sensor_tower import SensorTowerService

    cache_key = "rank:ios:US:com.fresh.x:d30"
    snapshot_payload = {"rankings": [{"date": "2026-05-08", "rank": 5}]}
    await quota.save_snapshot(cache_key, snapshot_payload)

    svc = SensorTowerService()
    svc.use_mock = False  # 强制走真实路径

    # 把 _get（httpx 出网）替换成会失败的 spy，这样如果路径错误就会暴露
    svc._get = AsyncMock(side_effect=AssertionError("应当不调用真实 API"))

    used_before = (await quota.current_usage())["used"]

    result = await svc._cached_get(cache_key, "/v1/x/y", {}, fallback=lambda: {"never": "used"})

    assert result == snapshot_payload, "应直接返回 SQLite 里的 fresh 快照"
    assert svc._get.await_count == 0, "fresh snapshot 命中时不能调 httpx"

    used_after = (await quota.current_usage())["used"]
    assert used_after == used_before, "fresh snapshot 命中不应消耗配额"


@pytest.mark.asyncio
async def test_cached_get_calls_api_and_writes_snapshot_on_l2_miss(client):
    """L1+L2 都 miss → 调 httpx、消耗一次配额、回写 snapshot。"""
    from app.services import quota
    from app.services.sensor_tower import SensorTowerService

    cache_key = "rank:ios:US:com.cold.y:d30"
    api_response = {"rankings": [{"date": "2026-05-09", "rank": 1}]}

    svc = SensorTowerService()
    svc.use_mock = False
    svc._get = AsyncMock(return_value=api_response)

    used_before = (await quota.current_usage())["used"]
    assert await quota.load_snapshot(cache_key) is None, "前置：无快照"

    result = await svc._cached_get(cache_key, "/v1/x/y", {}, fallback=lambda: {"fb": True})

    assert result == api_response
    assert svc._get.await_count == 1, "无快照时应该调一次真实 API"

    used_after = (await quota.current_usage())["used"]
    assert used_after == used_before + 1, "应消耗一次配额"

    # snapshot 已经被持久化
    assert await quota.load_snapshot(cache_key) == api_response


@pytest.mark.asyncio
async def test_force_refresh_bypasses_both_caches_and_consumes_quota(client):
    """force_refresh_today_rankings 必然调真实 API、消耗一次配额、写新 snapshot。"""
    from app.services import quota
    from app.services.sensor_tower import SensorTowerService
    from app.cache import sensor_tower_cache

    cache_key = "today:ios:US"
    stale_snapshot = {"apps": [{"app_id": "stale", "rank": 99}]}
    await quota.save_snapshot(cache_key, stale_snapshot)
    # 把它放进 L1 也填上（模拟 force refresh 之前刚被普通查询缓存过）
    await sensor_tower_cache.set(cache_key, stale_snapshot, ttl_seconds=86400)

    api_response = {"apps": [{"app_id": "fresh", "rank": 1, "name": "Fresh", "publisher": "Test"}]}

    svc = SensorTowerService()
    svc.use_mock = False
    svc._get = AsyncMock(return_value=api_response)

    used_before = (await quota.current_usage())["used"]

    result = await svc.force_refresh_today_rankings("US", "ios")

    assert result == api_response["apps"], "应返回真实 API 数据，不能是 stale 快照"
    assert svc._get.await_count == 1
    assert (await quota.current_usage())["used"] == used_before + 1
    # snapshot 已被覆盖为新数据
    assert await quota.load_snapshot(cache_key) == api_response


@pytest.mark.asyncio
async def test_force_refresh_endpoint_returns_fresh_data(client):
    """通过 router POST /api/games/rankings/refresh 走的端到端集成。"""
    # mock 模式下直接返回 mock 数据；测试主要验证路由可达 + 200
    resp = await client.post("/api/games/rankings/refresh", params={"country": "US", "platform": "ios"})
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) > 0


@pytest.mark.asyncio
async def test_get_injects_auth_token_as_query_param(client, monkeypatch):
    """根因修复：鉴权走 auth_token 查询参数，不是 Authorization: Bearer 头。"""
    import httpx
    from app.services.sensor_tower import SensorTowerService

    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    async def fake_get(self, url, params=None, **kw):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = kw.get("headers")
        return FakeResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    svc = SensorTowerService()
    svc.api_token = "TKN123"
    out = await svc._get("/v1/ios/ranking", {"country": "US"})

    assert out == {"ok": True}
    assert captured["params"]["auth_token"] == "TKN123", "密钥必须作为 auth_token 查询参数"
    assert captured["params"]["country"] == "US", "调用方参数保留"
    assert not captured["headers"], "不再发 Authorization 头"
    assert captured["url"].endswith("/v1/ios/ranking")


@pytest.mark.asyncio
async def test_get_all_rankings_today_parses_ranking_id_list(client):
    """/v1/{os}/ranking 返回有序 app_id 列表 → 转成 名次+app_id 行。"""
    from unittest.mock import patch
    from app.services.sensor_tower import SensorTowerService

    svc = SensorTowerService()
    svc.use_mock = False
    svc._get = AsyncMock(return_value={"ranking": ["553834731", "1053012308"]})

    with patch("app.services.sensor_tower.fetch_apps_bulk", AsyncMock(return_value={})):
        rows = await svc.get_all_rankings_today("US", "ios")

    assert [r["app_id"] for r in rows] == ["553834731", "1053012308"]
    assert [r["rank"] for r in rows] == [1, 2]
    assert rows[0]["name"] is None and rows[0]["downloads"] is None
    path, params = svc._get.call_args.args[0], svc._get.call_args.args[1]
    assert path == "/v1/ios/ranking"
    assert params["chart_type"] == "topfreeapplications"
    assert params["category"] == "6014"
    assert params["country"] == "US" and "date" in params


@pytest.mark.asyncio
async def test_get_all_rankings_today_enriches_names_via_itunes(client):
    """app_id 列表用 iTunes 批量补全名字/出版商/图标；查不到的保持 None。"""
    from unittest.mock import patch
    from app.services.sensor_tower import SensorTowerService

    svc = SensorTowerService()
    svc.use_mock = False
    svc._get = AsyncMock(return_value={"ranking": ["553834731", "999"]})
    meta = {"553834731": {"name": "Clash", "publisher": "Supercell",
                          "icon_url": "http://x/512.jpg"}}

    with patch("app.services.sensor_tower.fetch_apps_bulk", AsyncMock(return_value=meta)) as m:
        rows = await svc.get_all_rankings_today("JP", "ios")

    assert m.await_args.kwargs["country"] == "jp", "ST 国家码应转小写传给 iTunes"
    assert rows[0]["name"] == "Clash" and rows[0]["publisher"] == "Supercell"
    assert rows[0]["icon_url"] == "http://x/512.jpg"
    assert rows[1]["name"] is None, "iTunes 查不到的保持 None（前端字母兜底）"


@pytest.mark.asyncio
async def test_failed_fetch_refunds_quota_and_logs_error(client, caplog):
    """_get 失败：配额必须退还（净消耗 0）、降级到 fallback、并打 ERROR（进 Sentry）。"""
    import logging
    import httpx
    from app.services import quota
    from app.services.sensor_tower import SensorTowerService

    cache_key = "rank:ios:US:com.broken.q:d30"
    svc = SensorTowerService()
    svc.use_mock = False
    svc._get = AsyncMock(side_effect=httpx.HTTPError("boom"))

    used_before = (await quota.current_usage())["used"]
    with caplog.at_level(logging.ERROR, logger="app.services.sensor_tower"):
        result = await svc._cached_get(cache_key, "/v1/x/y", {}, fallback=lambda: {"fb": True})

    assert result == {"fb": True}, "失败应降级到 fallback"
    assert svc._get.await_count == 1
    assert (await quota.current_usage())["used"] == used_before, \
        "失败调用必须退还配额（净消耗 0）"
    assert any(
        "fetch failed" in r.getMessage()
        for r in caplog.records
        if r.name == "app.services.sensor_tower" and r.levelno >= logging.ERROR
    ), "失败应打 ERROR 级日志（→ Sentry）"


@pytest.mark.asyncio
async def test_cached_get_serves_stale_snapshot_when_quota_exhausted(client):
    """配额耗尽时，过期 snapshot 也能用作降级数据，不再调 API。"""
    from app.services import quota
    from app.services.sensor_tower import SensorTowerService
    from app.config import settings
    from sqlalchemy import text
    from app.database import AsyncSessionLocal

    cache_key = "rank:ios:US:com.stale.z:d30"
    stale_payload = {"rankings": [{"date": "2026-04-01", "rank": 99}]}
    await quota.save_snapshot(cache_key, stale_payload)

    # 把 snapshot 时间回退 48 小时，使其不在 24h 新鲜窗口内
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE sensor_tower_snapshots SET updated_at = datetime('now', '-48 hours') "
                "WHERE cache_key = :k"
            ).bindparams(k=cache_key)
        )
        await session.commit()

    svc = SensorTowerService()
    svc.use_mock = False
    svc._get = AsyncMock(side_effect=AssertionError("配额耗尽时不应该调 API"))

    # 把 limit 调到 0，模拟"配额耗尽"
    with patch.object(settings, "SENSOR_TOWER_MONTHLY_LIMIT", 0):
        result = await svc._cached_get(cache_key, "/v1/x/y", {}, fallback=lambda: {"fb": True})

    assert result == stale_payload, "配额耗尽时应回退到任意快照（即使过期）"
    assert svc._get.await_count == 0
