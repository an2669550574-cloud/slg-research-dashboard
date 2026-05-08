async def test_seed_then_list(client):
    seed = await client.get("/api/games/seed")
    assert seed.status_code == 200
    assert "已初始化" in seed.json()["message"]

    listing = await client.get("/api/games/")
    assert listing.status_code == 200
    games = listing.json()
    assert len(games) >= 8
    # response_model 必须脱掉 SQLAlchemy 内部字段
    for g in games:
        assert "_sa_instance_state" not in g
        assert "id" in g and "app_id" in g and "name" in g

    # 分页：limit=2 取前两条；X-Total-Count 体现全集大小
    paged = await client.get("/api/games/", params={"limit": 2})
    assert len(paged.json()) == 2
    assert int(paged.headers["x-total-count"]) >= 8


async def test_filter_and_search(client):
    await client.get("/api/games/seed")

    # 模糊搜索
    r = await client.get("/api/games/", params={"q": "Clash"})
    assert r.status_code == 200
    names = [g["name"] for g in r.json()]
    assert any("Clash" in n for n in names)

    # 按平台过滤（mock 数据全部 ios）
    r = await client.get("/api/games/", params={"platform": "ios"})
    assert all(g["platform"] == "ios" for g in r.json())

    r = await client.get("/api/games/", params={"platform": "android"})
    assert r.json() == []


async def test_create_duplicate_rejected(client):
    payload = {"app_id": "com.test.unique", "name": "Test Game"}
    r1 = await client.post("/api/games/", json=payload)
    assert r1.status_code == 201
    assert r1.json()["app_id"] == "com.test.unique"

    r2 = await client.post("/api/games/", json=payload)
    assert r2.status_code == 400


async def test_get_404_when_missing(client):
    r = await client.get("/api/games/com.does.not.exist")
    assert r.status_code == 404


async def test_metrics_default_window(client):
    r = await client.get("/api/games/com.lilithgames.rok/metrics", params={"days": 7})
    assert r.status_code == 200
    body = r.json()
    assert {"rankings", "downloads", "revenue"} <= body.keys()
    assert len(body["rankings"]) == 7
    assert len(body["downloads"]) == 7
    assert len(body["revenue"]) == 7


async def test_metrics_custom_range(client):
    r = await client.get(
        "/api/games/com.lilithgames.rok/metrics",
        params={"start_date": "2026-04-01", "end_date": "2026-04-05"},
    )
    assert r.status_code == 200
    body = r.json()
    # 4-1 至 4-5 共 5 天
    assert len(body["downloads"]) == 5
    assert body["downloads"][0]["date"] == "2026-04-01"
    assert body["downloads"][-1]["date"] == "2026-04-05"


async def test_rankings_today_returns_list(client):
    r = await client.get("/api/games/rankings")
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    assert len(items) > 0
    for it in items:
        assert "app_id" in it
        assert "rank" in it


async def test_delete_game_then_404(client):
    await client.post("/api/games/", json={"app_id": "com.test.todelete", "name": "X"})
    r = await client.delete("/api/games/com.test.todelete")
    assert r.status_code == 200
    assert r.json()["app_id"] == "com.test.todelete"

    r = await client.get("/api/games/com.test.todelete")
    assert r.status_code == 404


async def test_delete_missing_game_returns_404(client):
    r = await client.delete("/api/games/com.never.existed")
    assert r.status_code == 404


async def test_update_partial_fields(client):
    await client.post("/api/games/", json={"app_id": "com.test.upd", "name": "Old", "publisher": "Pub A"})
    r = await client.put("/api/games/com.test.upd", json={"name": "New"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "New"
    # 未传字段保留原值
    assert body["publisher"] == "Pub A"


async def test_update_missing_returns_404(client):
    r = await client.put("/api/games/com.does.not.exist", json={"name": "Whatever"})
    assert r.status_code == 404


async def test_lookup_invalid_app_id_returns_404(client):
    """非数字 ID 不会命中 iTunes，应得 404 而非 500。"""
    r = await client.post("/api/games/lookup", params={"app_id": "not-a-numeric-id"})
    assert r.status_code == 404


async def test_sync_rankings_writes_records(client):
    """手动触发同步会落库到 game_rankings 表（mock 模式下也走完整路径）。"""
    r = await client.post("/api/games/sync-rankings", params={"country": "US", "platform": "ios"})
    assert r.status_code == 200
    body = r.json()
    assert body["country"] == "US"
    assert body["platform"] == "ios"
    assert "已写入" in body["message"]


async def test_create_with_partial_payload_requires_name(client):
    """关键字段缺失且 iTunes 查询失败时应 400。"""
    r = await client.post("/api/games/", json={"app_id": "com.bogus.notinitunes"})
    assert r.status_code == 400
