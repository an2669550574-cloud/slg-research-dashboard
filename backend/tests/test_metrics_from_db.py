"""真实模式下 /api/games/{id}/metrics 的下载/收入应读本地 game_rankings
（回填+调度），ST 仅在库里无销量覆盖时兜底。conftest 重载 app.* → import 内置。

测试环境 USE_MOCK_DATA=true，故需 monkeypatch use_mock=False 才能走真实分支。
"""
import pytest
from datetime import timedelta
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


@pytest.mark.asyncio
async def test_today_rankings_carry_forward_sales(client):
    """销量周级解耦：今日榜行 dl/rev=NULL（非抓取日）时，日榜读路径用该 app
    上次已知销量兜底展示，零配额，不回写库。"""
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.game import GameRanking
    today = utcnow_naive().strftime("%Y-%m-%d")
    earlier = (utcnow_naive() - timedelta(days=3)).strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        # 三天前的抓取日：有真实销量
        db.add(GameRanking(app_id="com.carry.x", date=earlier, rank=7,
                           downloads=777, revenue=888.0, country="US", platform="ios",
                           name="Carry X", publisher="P"))
        # 今天：非抓取日，销量留 NULL
        db.add(GameRanking(app_id="com.carry.x", date=today, rank=5,
                           downloads=None, revenue=None, country="US", platform="ios",
                           name="Carry X", publisher="P"))
        # 对照：从未有过销量的 app，今日也 NULL → 兜底无值，保持 NULL
        db.add(GameRanking(app_id="com.never.y", date=today, rank=6,
                           downloads=None, revenue=None, country="US", platform="ios",
                           name="Never Y", publisher="P"))
        await db.commit()

    r = await client.get("/api/games/rankings", params={"country": "US", "platform": "ios"})
    assert r.status_code == 200
    by_id = {row["app_id"]: row for row in r.json()}
    # 有历史销量 → 兜底回上次已知值
    assert by_id["com.carry.x"]["downloads"] == 777
    assert by_id["com.carry.x"]["revenue"] == 888.0
    # 无任何历史销量 → 仍为 None（不臆造）
    assert by_id["com.never.y"]["downloads"] is None
    assert by_id["com.never.y"]["revenue"] is None


@pytest.mark.asyncio
async def test_today_rankings_carry_forward_not_persisted(client):
    """兜底仅用于展示，绝不回写库：详情页趋势仍读真实 NULL 行（诚实周级点）。"""
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.game import GameRanking
    from sqlalchemy import select
    today = utcnow_naive().strftime("%Y-%m-%d")
    earlier = (utcnow_naive() - timedelta(days=2)).strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        db.add(GameRanking(app_id="com.persist.z", date=earlier, rank=3,
                           downloads=111, revenue=222.0, country="US", platform="ios"))
        db.add(GameRanking(app_id="com.persist.z", date=today, rank=2,
                           downloads=None, revenue=None, country="US", platform="ios"))
        await db.commit()

    await client.get("/api/games/rankings", params={"country": "US", "platform": "ios"})

    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(GameRanking).where(
                GameRanking.app_id == "com.persist.z", GameRanking.date == today)
        )).scalar_one()
        assert row.downloads is None, "兜底不得回写库"
        assert row.revenue is None
