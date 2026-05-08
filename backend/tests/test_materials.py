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
