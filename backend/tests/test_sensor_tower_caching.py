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

    svc = SensorTowerService()
    svc.use_mock = False
    cache_key, _, _ = svc._today_key("US", "ios")  # key 含 chart_type+category

    stale_snapshot = {"apps": [{"app_id": "stale", "rank": 99}]}
    await quota.save_snapshot(cache_key, stale_snapshot)
    # 把它放进 L1 也填上（模拟 force refresh 之前刚被普通查询缓存过）
    await sensor_tower_cache.set(cache_key, stale_snapshot, ttl_seconds=86400)

    api_response = {"apps": [{"app_id": "fresh", "rank": 1, "name": "Fresh", "publisher": "Test"}]}
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
    from app.config import settings

    svc = SensorTowerService()
    svc.use_mock = False
    svc._get = AsyncMock(return_value={"ranking": ["553834731", "1053012308"]})

    # 隔离掉 sales 批量补全（topn=0），让 _get.call_args 仍是排行榜那次调用
    with patch("app.services.sensor_tower.fetch_apps_bulk", AsyncMock(return_value={})), \
         patch.object(settings, "SENSOR_TOWER_RANKING_SALES_TOPN", 0):
        rows = await svc.get_all_rankings_today("US", "ios")

    assert [r["app_id"] for r in rows] == ["553834731", "1053012308"]
    assert [r["rank"] for r in rows] == [1, 2]
    assert rows[0]["name"] is None and rows[0]["downloads"] is None
    path, params = svc._get.call_args.args[0], svc._get.call_args.args[1]
    assert path == "/v1/ios/ranking"
    assert params["chart_type"] == "topgrossingapplications"
    assert params["category"] == "7017"
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


def test_parse_sales_ios_sums_iphone_ipad_and_cents_to_dollars():
    from app.services.sensor_tower import _parse_sales

    raw = [
        {"aid": "1", "d": "2026-05-01", "iu": 100, "au": 20, "ir": 5000, "ar": 1000},
        {"aid": "1", "d": "2026-05-02", "iu": 50, "au": 0, "ir": 300, "ar": None},
    ]
    out = _parse_sales(raw, "ios")
    assert out["downloads"] == [
        {"date": "2026-05-01", "value": 120}, {"date": "2026-05-02", "value": 50}
    ]
    assert out["revenue"] == [
        {"date": "2026-05-01", "value": 60.0}, {"date": "2026-05-02", "value": 3.0}
    ]


def test_parse_sales_android_uses_u_r_keys():
    from app.services.sensor_tower import _parse_sales

    # ST 的 d 是 ISO 时间戳；必须截到日，否则和本地排名序列 X 轴对不齐
    raw = [{"aid": "com.x", "d": "2026-05-01T00:00:00Z", "u": 999, "r": 12345}]
    out = _parse_sales(raw, "android")
    assert out["downloads"] == [{"date": "2026-05-01", "value": 999}]
    assert out["revenue"] == [{"date": "2026-05-01", "value": 123.45}]


def test_parse_sales_passthrough_fallback_shape():
    """fallback / mock 已是目标形状 → 原样透传，不二次解析。"""
    from app.services.sensor_tower import _parse_sales

    shaped = {"downloads": [{"date": "d", "value": 1}], "revenue": [{"date": "d", "value": 2}]}
    assert _parse_sales(shaped, "ios") == shaped


@pytest.mark.asyncio
async def test_get_sales_hits_sales_report_estimates_and_parses(client):
    from app.services.sensor_tower import SensorTowerService

    svc = SensorTowerService()
    svc.use_mock = False
    svc._get = AsyncMock(return_value=[{"aid": "1", "d": "2026-05-01", "iu": 10, "au": 5, "ir": 200, "ar": 0}])

    out = await svc.get_sales("1", country="US", platform="ios", days=7)

    assert out["downloads"] == [{"date": "2026-05-01", "value": 15}]
    assert out["revenue"] == [{"date": "2026-05-01", "value": 2.0}]
    path, params = svc._get.call_args.args[0], svc._get.call_args.args[1]
    assert path == "/v1/ios/sales_report_estimates"
    assert params["app_ids"] == "1" and params["countries"] == "US"
    assert params["date_granularity"] == "daily"
    assert "start_date" in params and "end_date" in params


@pytest.mark.asyncio
async def test_fetch_play_apps_parses_jsonld_and_skips_numeric(client, monkeypatch):
    import httpx
    from app.services.appstore import fetch_play_apps

    html = (
        '<html><head><script type="application/ld+json" nonce="z">'
        '{"@type":"SoftwareApplication","name":"Rise of Kingdoms",'
        '"image":"https://lh/icon.png","author":{"@type":"Organization","name":"Lilith Games"}}'
        '</script></head></html>'
    )

    class R:
        status_code = 200
        text = html

    async def fake_get(self, url, params=None, **kw):
        return R()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await fetch_play_apps(["com.lilithgames.rok", "123456"], country="us")

    assert "123456" not in out, "纯数字 id（iOS）应被跳过"
    assert out["com.lilithgames.rok"] == {
        "name": "Rise of Kingdoms", "publisher": "Lilith Games",
        "icon_url": "https://lh/icon.png",
    }


@pytest.mark.asyncio
async def test_fetch_play_apps_caps_and_degrades(client, monkeypatch):
    import httpx
    from app.services.appstore import fetch_play_apps

    calls = []

    class R:
        status_code = 404
        text = ""

    async def fake_get(self, url, params=None, **kw):
        calls.append(params["id"])
        return R()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await fetch_play_apps(["com.a", "com.b", "com.c", "com.d"], max_apps=2)

    assert out == {}, "非 200 静默降级为空，不抛"
    assert len(calls) == 2, "max_apps 必须限量请求数"


@pytest.mark.asyncio
async def test_android_ranking_enriched_via_play_not_itunes(client):
    from unittest.mock import patch
    from app.services.sensor_tower import SensorTowerService

    svc = SensorTowerService()
    svc.use_mock = False
    svc._get = AsyncMock(return_value={"ranking": ["com.x.y"]})
    pm = {"com.x.y": {"name": "X", "publisher": "P", "icon_url": "i"}}

    with patch("app.services.sensor_tower.fetch_play_apps", AsyncMock(return_value=pm)) as fp, \
         patch("app.services.sensor_tower.fetch_apps_bulk", AsyncMock(return_value={})) as fb:
        rows = await svc.get_all_rankings_today("US", "android")

    fp.assert_awaited()
    fb.assert_not_awaited()
    assert rows[0]["app_id"] == "com.x.y" and rows[0]["name"] == "X"


def test_parse_sales_by_app_ios_takes_latest_day():
    from app.services.sensor_tower import _parse_sales_by_app
    raw = [
        {"aid": "1", "d": "2026-05-01", "iu": 10, "au": 2, "ir": 500, "ar": 0},
        {"aid": "1", "d": "2026-05-03T00:00:00Z", "iu": 20, "au": 0, "ir": 700, "ar": 300},
        {"aid": "2", "d": "2026-05-02", "iu": 5, "au": 0, "ir": 100, "ar": 0},
    ]
    out = _parse_sales_by_app(raw, "ios")
    assert out["1"] == {"downloads": 20, "revenue": 10.0}, "只取最新一天 (05-03)"
    assert out["2"] == {"downloads": 5, "revenue": 1.0}


def test_parse_sales_by_app_android_and_empty():
    from app.services.sensor_tower import _parse_sales_by_app
    raw = [{"aid": "com.x", "d": "2026-05-01T00:00:00Z", "u": 999, "r": 12345}]
    assert _parse_sales_by_app(raw, "android") == {"com.x": {"downloads": 999, "revenue": 123.45}}
    assert _parse_sales_by_app({}, "ios") == {}
    assert _parse_sales_by_app([], "ios") == {}


@pytest.mark.asyncio
async def test_get_sales_batch_single_call_comma_ids(client):
    """前 N 名一次批量调用（app_ids 逗号），按 app 解析；mock/空短路。"""
    from app.services.sensor_tower import SensorTowerService

    svc = SensorTowerService()
    svc.use_mock = False
    svc._get = AsyncMock(return_value=[
        {"aid": "111", "d": "2026-05-02", "iu": 7, "au": 1, "ir": 900, "ar": 100},
        {"aid": "222", "d": "2026-05-02", "iu": 3, "au": 0, "ir": 50, "ar": 0},
    ])

    out = await svc.get_sales_batch(["111", "222"], "US", "ios")

    assert out["111"] == {"downloads": 8, "revenue": 10.0}
    assert out["222"] == {"downloads": 3, "revenue": 0.5}
    path, params = svc._get.call_args.args[0], svc._get.call_args.args[1]
    assert path == "/v1/ios/sales_report_estimates"
    assert params["app_ids"] == "111,222"
    assert svc._get.await_count == 1, "批量必须是单次调用"

    assert await svc.get_sales_batch([], "US", "ios") == {}
    svc.use_mock = True
    assert await svc.get_sales_batch(["x"], "US", "ios") == {}


@pytest.mark.asyncio
async def test_ranking_topn_filled_rest_left_null(client):
    """前 N 名补真实下载/收入；榜尾保持 None（前端显示 —）。"""
    from unittest.mock import patch
    from app.services.sensor_tower import SensorTowerService

    svc = SensorTowerService()
    svc.use_mock = False
    svc._get = AsyncMock(return_value={"ranking": ["111", "222", "333"]})

    with patch("app.services.sensor_tower.fetch_apps_bulk", AsyncMock(return_value={})), \
         patch.object(svc, "get_sales_batch",
                      AsyncMock(return_value={"111": {"downloads": 9, "revenue": 99.0}})):
        rows = await svc.get_all_rankings_today("US", "ios")

    assert rows[0]["downloads"] == 9 and rows[0]["revenue"] == 99.0
    assert rows[1]["downloads"] is None and rows[2]["revenue"] is None


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


def test_parse_featured_impacts_known_shapes():
    """_parse_featured_impacts 可解析 list 型和 dict 包裹型响应。"""
    from app.services.sensor_tower import _parse_featured_impacts

    # 裸列表形式
    raw_list = [
        {"date": "2024-03-01", "slot_name": "Today Story", "country": "US", "downloads": 18000},
        {"date": "2023-11-10", "slot_name": "Apps & Games", "country": "CN"},
    ]
    events = _parse_featured_impacts(raw_list)
    assert len(events) == 2
    assert events[0]["event_type"] == "featuring"
    assert events[0]["event_date"] == "2024-03-01"
    assert "Today Story" in events[0]["description"]
    assert "18,000" in events[0]["description"]
    assert events[0]["title"] == "App Store Today 故事推荐"

    # dict 包裹形式
    wrapped = {"featured_impacts": raw_list}
    assert _parse_featured_impacts(wrapped) == events

    # 未知响应 → 空
    assert _parse_featured_impacts({}) == []
    assert _parse_featured_impacts([]) == []
    assert _parse_featured_impacts({"data": []}) == []


def test_parse_featured_impacts_unknown_slot_kept_as_is():
    from app.services.sensor_tower import _parse_featured_impacts
    rows = [{"date": "2025-06-01", "slot_name": "New Games We Love", "country": "US"}]
    events = _parse_featured_impacts(rows)
    assert len(events) == 1
    assert events[0]["title"] == "New Games We Love"


def test_parse_featured_impacts_missing_date_skipped():
    from app.services.sensor_tower import _parse_featured_impacts
    rows = [{"slot_name": "Today Story", "country": "US", "downloads": 5000}]
    assert _parse_featured_impacts(rows) == []
