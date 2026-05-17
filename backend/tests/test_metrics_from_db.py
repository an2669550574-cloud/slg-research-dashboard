"""真实模式下 /api/games/{id}/metrics 的下载/收入应读本地 game_rankings
（回填+调度），ST 仅在库里无销量覆盖时兜底。conftest 重载 app.* → import 内置。

测试环境 USE_MOCK_DATA=true，故需 monkeypatch use_mock=False 才能走真实分支。
"""
import pytest
from unittest.mock import patch, AsyncMock

WIN = {"country": "US", "platform": "ios",
       "start_date": "2026-05-10", "end_date": "2026-05-17"}


async def _seed(rows):
    """rows: list of (app_id, date, rank, downloads, revenue)。"""
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking
    async with AsyncSessionLocal() as db:
        for aid, d, rk, dl, rv in rows:
            db.add(GameRanking(app_id=aid, date=d, rank=rk, downloads=dl, revenue=rv,
                               country="US", platform="ios"))
        await db.commit()


@pytest.mark.asyncio
async def test_covered_app_serves_from_db_no_st(client, monkeypatch):
    from app.routers import games
    monkeypatch.setattr(games.sensor_tower_service, "use_mock", False)
    await _seed([
        ("covered.1", "2026-05-15", 5, 1000, 50000.0),
        ("covered.1", "2026-05-16", None, 2000, 60000.0),  # 回填行 rank=NULL
        ("covered.1", "2026-05-17", 3, 3000, 70000.0),
    ])
    spy = AsyncMock()
    with patch.object(games.sensor_tower_service, "get_sales", new=spy):
        r = await client.get("/api/games/covered.1/metrics", params=WIN)
    assert r.status_code == 200
    body = r.json()
    # rank=NULL 的回填行不进排名走势
    assert [p["date"] for p in body["rankings"]] == ["2026-05-15", "2026-05-17"]
    # 下载/收入含全部 3 天（含回填行），来自 DB
    assert [p["value"] for p in body["downloads"]] == [1000, 2000, 3000]
    assert [p["value"] for p in body["revenue"]] == [50000.0, 60000.0, 70000.0]
    spy.assert_not_awaited()  # 关键：库覆盖时不打 ST，零配额


@pytest.mark.asyncio
async def test_ranked_but_no_sales_falls_back_to_st(client, monkeypatch):
    """有 rank 行但销量全 NULL（非 Top50 回填）→ 回退 ST 取下载收入，图表不空。"""
    from app.routers import games
    monkeypatch.setattr(games.sensor_tower_service, "use_mock", False)
    await _seed([("rankonly.1", "2026-05-17", 42, None, None)])
    fake = {"downloads": [{"date": "2026-05-17", "value": 9}],
            "revenue": [{"date": "2026-05-17", "value": 8}]}
    with patch.object(games.sensor_tower_service, "get_sales",
                      new=AsyncMock(return_value=fake)) as spy:
        r = await client.get("/api/games/rankonly.1/metrics", params=WIN)
    assert r.status_code == 200
    body = r.json()
    assert body["rankings"] == [{"date": "2026-05-17", "value": None, "rank": 42}]
    assert body["downloads"] == [{"date": "2026-05-17", "value": 9, "rank": None}]
    spy.assert_awaited_once()


@pytest.mark.asyncio
async def test_uncovered_app_falls_back_to_st(client, monkeypatch):
    """库里完全无该 app（未同步市场/未回填）→ 排名空 + ST 兜底销量。"""
    from app.routers import games
    monkeypatch.setattr(games.sensor_tower_service, "use_mock", False)
    fake = {"downloads": [{"date": "2026-05-17", "value": 1}],
            "revenue": [{"date": "2026-05-17", "value": 2}]}
    with patch.object(games.sensor_tower_service, "get_sales",
                      new=AsyncMock(return_value=fake)) as spy:
        r = await client.get("/api/games/nobody.zzz/metrics", params=WIN)
    assert r.status_code == 200
    body = r.json()
    assert body["rankings"] == []
    assert [p["value"] for p in body["downloads"]] == [1]
    spy.assert_awaited_once()
