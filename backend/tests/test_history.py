"""发展历程：CRUD + 数据驱动同步（事实性，无 AI）。

conftest 每个 test 重载 app.* —— app.* import 放函数内。iTunes 查询用
AsyncMock 打桩，保持 hermetic（不打真实网络、不 flaky）。
"""
from unittest.mock import patch, AsyncMock, MagicMock


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
        "name": "Kingshot", "publisher": "Century Games",
        "release_date": "2024-04-02",
        "current_version_date": "2026-05-10",
        "version": "1.5.0",
        "release_notes": "新增賽季玩法",  # tw 命中 → 文案应原样采用
        "description": "marketing blurb (ignored)",
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
    by_title = {e["title"]: e for e in events}
    # 上线事件用中文事实句，不贴英文营销文案
    launch = next(e for e in events if e["event_type"] == "launch")
    assert "Kingshot（Century Games）" in launch["description"]
    assert "blurb" not in launch["description"]
    # 版本事件采用繁中更新说明
    ver = next(e for e in events if e["event_type"] == "version")
    assert ver["description"] == "新增賽季玩法"
    # 8→2 的爬升应产出「最高排名 #2」与「首次进入 Top 3」
    assert any("#2" in t for t in by_title)
    assert any("Top 3" in t for t in by_title)
    assert [e["event_date"] for e in events] == sorted(e["event_date"] for e in events)


async def test_sync_no_sources_yields_empty_but_200(client):
    """Android 包名 iTunes 查不到 + 无 game_rankings → 0 事实事件，仍 200 不报错。"""
    with patch("app.services.history_builder.fetch_app_info",
               new=AsyncMock(return_value=None)):
        r = await client.post("/api/history/sync/com.unknown.pkg")
    assert r.status_code == 200
    assert (await client.get("/api/history/com.unknown.pkg")).json() == []


async def test_sync_android_borrows_itunes_from_ios_sibling(client):
    """Android 包名 iTunes 直查 None → 借同款 iOS 姐妹 app_id 的 iTunes 元信息，
    让 Android 详情页也能看到上线日 + 当前版本。借来的版本标题加 'iOS' 前缀以
    明示来源。"""
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking

    # 灌两条 game_rankings 建立姐妹关系（同 publisher + 同 normalized 名字）
    async with AsyncSessionLocal() as db:
        db.add(GameRanking(
            app_id="6448786147", date="2026-05-17", rank=1, downloads=100,
            revenue=10000.0, country="US", platform="ios",
            name="Last War:Survival", publisher="FUNFLY PTE. LTD."))
        db.add(GameRanking(
            app_id="com.fun.lastwar.gp", date="2026-05-17", rank=1, downloads=80,
            revenue=8000.0, country="US", platform="android",
            name="Last War:Survival Game", publisher="FUNFLY PTE. LTD."))
        await db.commit()

    ios_itunes = {
        "name": "Last War:Survival", "publisher": "FUNFLY PTE. LTD.",
        "release_date": "2023-06-30",
        "current_version_date": "2026-05-15",
        "version": "1.0.230",
        "release_notes": "新增春季祭活動",
    }

    # 关键：Android 包名首次查 → None；iOS 数字 id 才返数据
    async def fake_fetch(app_id, country="us"):
        if app_id == "6448786147":
            return ios_itunes
        return None

    with patch("app.services.history_builder.fetch_app_info", new=AsyncMock(side_effect=fake_fetch)):
        # 调 Android 变体的同步：应当从 iOS 姐妹借数据
        r = await client.post("/api/history/sync/com.fun.lastwar.gp")
        assert r.status_code == 200

        events = (await client.get("/api/history/com.fun.lastwar.gp")).json()
        titles = [e["title"] for e in events]
        # 上线事件借自 iOS 姐妹
        assert any("App Store 全球上线" in t for t in titles), titles
        # 版本事件加了 "iOS" 前缀，明示数据来源
        assert any("iOS 更新至 v1.0.230" in t for t in titles), titles

        # 反例：iOS 详情页同步时不应有 iOS 前缀（原地就是 iOS 数据，无需标注）
        r2 = await client.post("/api/history/sync/6448786147")
        assert r2.status_code == 200
        ios_titles = [e["title"] for e in (await client.get("/api/history/6448786147")).json()]
        assert any("更新至 v1.0.230" in t for t in ios_titles)
        assert not any("iOS 更新至" in t for t in ios_titles), ios_titles


async def test_sync_includes_featuring_events_for_ios_app(client):
    """iOS 数字 app_id 同步时，featured/impacts 返回的推荐事件写入历程；
    Android 包名跳过（get_featured_impacts 从不被调）。"""
    await _seed_rankings("1234567890")
    featured_data = [
        {"date": "2024-03-01", "slot_name": "Today Story", "country": "US", "downloads": 18000},
        {"date": "2023-11-10", "slot_name": "Apps & Games", "country": "CN", "downloads": None},
    ]
    with (
        patch("app.services.history_builder.fetch_app_info", new=AsyncMock(return_value=None)),
        patch(
            "app.services.history_builder.sensor_tower_service.get_featured_impacts",
            new=AsyncMock(return_value=[
                {"event_date": "2024-03-01", "event_type": "featuring",
                 "title": "App Store Today 故事推荐",
                 "description": "US App Store 推荐位：Today Story，期间下载增益约 18,000。"},
                {"event_date": "2023-11-10", "event_type": "featuring",
                 "title": "App Store 精选推荐",
                 "description": "CN App Store 推荐位：Apps & Games。"},
            ]),
        ),
    ):
        r = await client.post("/api/history/sync/1234567890")
    assert r.status_code == 200
    events = (await client.get("/api/history/1234567890")).json()
    featuring = [e for e in events if e["event_type"] == "featuring"]
    assert len(featuring) == 2
    assert any("Today Story" in e["description"] for e in featuring)
    # 事件按日期升序
    dates = [e["event_date"] for e in events]
    assert dates == sorted(dates)


async def test_featuring_silently_skipped_on_failure(client):
    """featured/impacts 失败时不抛，已有历程事件不受影响。"""
    await _seed_rankings("9999000001")
    with (
        patch("app.services.history_builder.fetch_app_info", new=AsyncMock(return_value=None)),
        patch(
            "app.services.history_builder.sensor_tower_service.get_featured_impacts",
            new=AsyncMock(side_effect=Exception("ST down")),
        ),
    ):
        r = await client.post("/api/history/sync/9999000001")
    assert r.status_code == 200
    events = (await client.get("/api/history/9999000001")).json()
    assert all(e["event_type"] != "featuring" for e in events)
    assert any(e["event_type"] == "ranking" for e in events)


async def test_featuring_skipped_for_android_package(client):
    """Android 包名不调 get_featured_impacts（包名非纯数字）。"""
    mock_featured = AsyncMock(return_value=[])
    with (
        patch("app.services.history_builder.fetch_app_info", new=AsyncMock(return_value=None)),
        patch(
            "app.services.history_builder.sensor_tower_service.get_featured_impacts",
            new=mock_featured,
        ),
    ):
        r = await client.post("/api/history/sync/com.android.example")
    assert r.status_code == 200
    mock_featured.assert_not_called()


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
