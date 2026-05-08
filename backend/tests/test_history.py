async def test_create_get_delete_history(client):
    create = await client.post("/api/history/", json={
        "app_id": "com.test.history",
        "event_date": "2026-01-15",
        "event_type": "version",
        "title": "Big update",
        "description": "Added new content",
    })
    assert create.status_code == 201
    body = create.json()
    assert body["title"] == "Big update"
    event_id = body["id"]

    listed = await client.get("/api/history/com.test.history")
    assert listed.status_code == 200
    assert any(e["id"] == event_id for e in listed.json())

    deleted = await client.delete(f"/api/history/{event_id}")
    assert deleted.status_code == 200

    after = await client.get("/api/history/com.test.history")
    assert all(e["id"] != event_id for e in after.json())


async def test_ai_sync_uses_curated_mock(client):
    """已知 app_id 走内置 MOCK_HISTORIES，不需要真实 Anthropic API。"""
    r = await client.post("/api/history/sync/com.lilithgames.rok")
    assert r.status_code == 200
    assert "已同步" in r.json()["message"]

    listed = await client.get("/api/history/com.lilithgames.rok")
    events = listed.json()
    assert len(events) >= 5
    assert all(e["source"] == "ai" for e in events)


async def test_ai_sync_preserves_manual_events(client):
    # 先添加一条手动事件
    manual = await client.post("/api/history/", json={
        "app_id": "com.lilithgames.rok",
        "event_date": "2026-02-01",
        "event_type": "marketing",
        "title": "Manual entry",
        "source": "manual",
    })
    manual_id = manual.json()["id"]

    # AI 同步应清掉旧 AI 数据但保留 manual
    await client.post("/api/history/sync/com.lilithgames.rok")

    listed = await client.get("/api/history/com.lilithgames.rok")
    events = listed.json()
    assert any(e["id"] == manual_id for e in events)
