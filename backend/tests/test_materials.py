async def test_crud_lifecycle(client):
    # create
    r = await client.post("/api/materials/", json={
        "app_id": "com.test.mat",
        "title": "Promo video",
        "url": "https://youtube.com/watch?v=abc",
        "platform": "youtube",
        "material_type": "video",
        "tags": ["epic", "battle"],
        "notes": "First creative",
    })
    assert r.status_code == 201
    mid = r.json()["id"]
    assert r.json()["tags"] == ["epic", "battle"]

    # update
    r = await client.put(f"/api/materials/{mid}", json={"notes": "Updated note"})
    assert r.status_code == 200
    assert r.json()["notes"] == "Updated note"
    # tags 未改动应保留
    assert r.json()["tags"] == ["epic", "battle"]

    # filter by app_id
    r = await client.get("/api/materials/", params={"app_id": "com.test.mat"})
    assert r.status_code == 200
    assert len(r.json()) == 1

    # delete
    r = await client.delete(f"/api/materials/{mid}")
    assert r.status_code == 200

    r = await client.get("/api/materials/", params={"app_id": "com.test.mat"})
    assert r.json() == []


async def test_filter_by_platform_and_type(client):
    for i, plat in enumerate(["youtube", "tiktok", "youtube"]):
        await client.post("/api/materials/", json={
            "app_id": f"com.test.{i}",
            "title": f"Material {i}",
            "url": f"https://example.com/{i}",
            "platform": plat,
            "material_type": "video",
        })

    r = await client.get("/api/materials/", params={"platform": "youtube"})
    assert all(m["platform"] == "youtube" for m in r.json())
    assert len(r.json()) == 2

    r = await client.get("/api/materials/", params={"platform": "tiktok"})
    assert len(r.json()) == 1


async def test_filter_by_tag_and_tags_aggregation(client):
    # CJK 标签：站内素材标签常是中文（"竖版"/"出海买量"），ASCII 夹具会漏掉真 bug
    fixtures = [
        ("com.cjk.a", "战斗集锦", ["竖版", "出海买量"]),
        ("com.cjk.a", "开屏画面", ["竖版"]),
        ("com.cjk.b", "横版预告", ["横版", "出海买量"]),
    ]
    for app_id, title, tags in fixtures:
        r = await client.post("/api/materials/", json={
            "app_id": app_id, "title": title,
            "url": "https://example.com/x", "tags": tags,
        })
        assert r.status_code == 201

    # 精确按标签筛选（中文）
    r = await client.get("/api/materials/", params={"tag": "竖版"})
    assert r.status_code == 200
    titles = sorted(m["title"] for m in r.json())
    assert titles == ["开屏画面", "战斗集锦"]
    assert int(r.headers["x-total-count"]) == 2

    # 标签 + app_id 叠加
    r = await client.get("/api/materials/", params={"tag": "出海买量", "app_id": "com.cjk.b"})
    assert [m["title"] for m in r.json()] == ["横版预告"]

    # 标签聚合：按热度降序
    r = await client.get("/api/materials/tags")
    assert r.status_code == 200
    agg = {row["tag"]: row["count"] for row in r.json()}
    assert agg == {"竖版": 2, "出海买量": 2, "横版": 1}
    assert r.json()[0]["count"] >= r.json()[-1]["count"]

    # 标签聚合按 app_id 限定
    r = await client.get("/api/materials/tags", params={"app_id": "com.cjk.a"})
    assert {row["tag"]: row["count"] for row in r.json()} == {"竖版": 2, "出海买量": 1}


async def test_update_can_relink_game_and_retag(client):
    r = await client.post("/api/materials/", json={
        "app_id": "com.old.game", "title": "复用素材",
        "url": "https://example.com/y", "tags": ["旧标签"],
    })
    mid = r.json()["id"]

    r = await client.put(f"/api/materials/{mid}", json={
        "app_id": "com.new.game", "tags": ["新标签", "已归类"],
    })
    assert r.status_code == 200
    assert r.json()["app_id"] == "com.new.game"
    assert r.json()["tags"] == ["新标签", "已归类"]

    # 旧游戏下查不到，新游戏下能查到
    assert (await client.get("/api/materials/", params={"app_id": "com.old.game"})).json() == []
    assert len((await client.get("/api/materials/", params={"app_id": "com.new.game"})).json()) == 1


async def test_pagination_total_count_header(client):
    for i in range(5):
        await client.post("/api/materials/", json={
            "app_id": "com.test.pag",
            "title": f"M{i}",
            "url": f"https://example.com/{i}",
        })

    r = await client.get("/api/materials/", params={"app_id": "com.test.pag", "limit": 2})
    assert r.status_code == 200
    assert len(r.json()) == 2
    assert int(r.headers["x-total-count"]) == 5
