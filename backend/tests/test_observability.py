async def test_health_endpoint_unauthenticated(client):
    """健康检查必须永远免鉴权，否则 LB / Caddy / Docker 会一直 fail。"""
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_deep_health_returns_all_checks(client):
    r = await client.get("/api/health/deep")
    assert r.status_code == 200
    body = r.json()
    assert "status" in body
    assert set(body["checks"].keys()) == {"database", "sensor_tower", "anthropic", "quota"}
    # mock 模式下 sensor_tower 与 anthropic 应该 skipped
    assert body["checks"]["sensor_tower"]["status"] == "skipped"
    assert body["checks"]["database"]["status"] == "ok"
    # 全新 DB、零用量 → quota ok，不拖垮整体状态
    assert body["checks"]["quota"]["status"] == "ok"
    assert body["status"] == "ok"


async def test_deep_health_degrades_only_on_quota_exhaustion(client):
    """配额耗尽 → degraded；仅越过告警线 → 仍 ok（不让探针抖动）。"""
    from unittest.mock import patch
    from app.services import quota
    from app.config import settings

    # 越过 80% 但未耗尽：limit=10，用 8 → warning 但 overall 仍 ok
    with patch.object(settings, "SENSOR_TOWER_MONTHLY_LIMIT", 10):
        for _ in range(8):
            await quota.try_consume()
        body = (await client.get("/api/health/deep")).json()
        assert body["checks"]["quota"]["status"] == "warning"
        assert body["status"] == "ok"

        # 再用满到 limit → exhausted → overall degraded
        for _ in range(2):
            await quota.try_consume()
        body = (await client.get("/api/health/deep")).json()
        assert body["checks"]["quota"]["status"] == "exhausted"
        assert body["status"] == "degraded"


async def test_cache_stats_endpoint(client):
    r = await client.get("/api/cache/stats")
    assert r.status_code == 200
    stats = r.json()["sensor_tower"]
    assert {"entries", "live", "inflight"} <= stats.keys()


async def test_request_id_header_set(client):
    r = await client.get("/api/health")
    assert r.headers.get("x-request-id"), "X-Request-ID must be added by middleware"


async def test_request_id_passthrough(client):
    """客户端传入 X-Request-ID 应被采纳，便于跨服务追踪。"""
    rid = "trace-abc-123"
    r = await client.get("/api/health", headers={"X-Request-ID": rid})
    assert r.headers["x-request-id"] == rid
