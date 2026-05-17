"""发展历程：CRUD + 数据驱动同步（事实性，无 AI）。

conftest 每个 test 重载 app.* —— app.* import 放函数内。iTunes 查询用
AsyncMock 打桩，保持 hermetic（不打真实网络、不 flaky）。
"""
from unittest.mock import patch, AsyncMock


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


async def _seed_rankings(app_id):
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking
    async with AsyncSessionLocal() as db:
        db.add(GameRanking(app_id=app_id, date="2026-05-16", rank=8, downloads=9000,
                            revenue=120000.0, country="US", platform="ios",
                            name="X", publisher="Y", icon_url=None))
        db.add(GameRanking(app_id=app_id, date="2026-05-17", rank=2, downloads=14000,
                            revenue=500000.0, country="US", platform="ios",
                            name="X", publisher="Y", icon_url=None))
        await db.commit()


async def test_sync_builds_factual_events(client):
    """iTunes 给上线/版本，game_rankings 给最高排名/收入峰值，全部 source=data。"""
    await _seed_rankings("6739554056")
    itunes = {
        "release_date": "2024-04-02",
        "current_version_date": "2026-05-10",
        "version": "1.5.0",
        "release_notes": "新增赛季玩法",
        "description": "一款 SLG",
    }
    with patch("app.services.history_builder.fetch_app_info",
               new=AsyncMock(return_value=itunes)):
        r = await client.post("/api/history/sync/6739554056")
    assert r.status_code == 200
    assert "已同步" in r.json()["message"]

    events = (await client.get("/api/history/6739554056")).json()
    assert all(e["source"] == "data" for e in events)
    types = {e["event_type"] for e in events}
    assert {"launch", "version", "ranking", "revenue"} <= types
    rank_ev = next(e for e in events if e["event_type"] == "ranking")
    assert "#2" in rank_ev["title"]            # 两天里最优名次
    assert [e["event_date"] for e in events] == sorted(e["event_date"] for e in events)


async def test_sync_no_sources_yields_empty_but_200(client):
    """Android 包名 iTunes 查不到 + 无 game_rankings → 0 事实事件，仍 200 不报错。"""
    with patch("app.services.history_builder.fetch_app_info",
               new=AsyncMock(return_value=None)):
        r = await client.post("/api/history/sync/com.unknown.pkg")
    assert r.status_code == 200
    assert (await client.get("/api/history/com.unknown.pkg")).json() == []


async def test_sync_preserves_manual_events(client):
    """重新同步只清 source!=manual，手动录入保留。"""
    await _seed_rankings("appZ")
    manual = await client.post("/api/history/", json={
        "app_id": "appZ", "event_date": "2026-02-01", "event_type": "marketing",
        "title": "Manual entry", "source": "manual",
    })
    manual_id = manual.json()["id"]

    with patch("app.services.history_builder.fetch_app_info",
               new=AsyncMock(return_value=None)):
        await client.post("/api/history/sync/appZ")

    events = (await client.get("/api/history/appZ")).json()
    assert any(e["id"] == manual_id and e["source"] == "manual" for e in events)
    assert any(e["source"] == "data" for e in events)  # rank/revenue 里程碑仍写入
