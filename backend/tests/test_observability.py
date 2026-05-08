async def test_health_endpoint_unauthenticated(client):
    """健康检查必须永远免鉴权，否则 LB / Caddy / Docker 会一直 fail。"""
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_deep_health_returns_all_three_checks(client):
    r = await client.get("/api/health/deep")
    assert r.status_code == 200
    body = r.json()
    assert "status" in body
    assert set(body["checks"].keys()) == {"database", "sensor_tower", "anthropic"}
    # mock 模式下 sensor_tower 与 anthropic 应该 skipped
    assert body["checks"]["sensor_tower"]["status"] == "skipped"
    assert body["checks"]["database"]["status"] == "ok"


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
