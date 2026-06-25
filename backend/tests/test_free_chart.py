"""下载/免费榜 chart_type 维度（ADR 0001，切片 1）。

核心不变量：
- 收入榜(grossing)与下载榜(free)在同 (app_id,date,country,platform) 并存不撞五元组唯一约束。
- chart_type='free' 同步走 board='free' 拉榜、落 chart_type='free' 行。
- **零回归**：所有现有读路径（今日榜 / 新品检测）只看 grossing，free 行不可见。
"""
import pytest
from datetime import timedelta
from unittest.mock import patch, AsyncMock


async def _count(chart_type, country="US", platform="ios"):
    from sqlalchemy import select, func
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.game import GameRanking
    today = utcnow_naive().strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(func.count()).select_from(GameRanking).where(
                GameRanking.date == today, GameRanking.country == country,
                GameRanking.platform == platform, GameRanking.chart_type == chart_type,
            )
        )).scalar_one()


@pytest.mark.asyncio
async def test_free_and_grossing_coexist_same_day(client):
    """同一 app 当天既在收入榜又在下载榜：两行并存，不撞唯一约束。"""
    from app import scheduler
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.game import GameRanking, CHART_GROSSING
    today = utcnow_naive().strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:  # 先有一行收入榜
        db.add(GameRanking(app_id="com.x.same", date=today, rank=3, country="US",
                           platform="ios", name="同款", publisher="厂",
                           chart_type=CHART_GROSSING))
        await db.commit()

    fresh = [{"app_id": "com.x.same", "rank": 1, "name": "同款", "publisher": "厂"}]
    with patch.object(scheduler.sensor_tower_service, "get_all_rankings_today",
                      new=AsyncMock(return_value=fresh)) as m:
        written = await scheduler.sync_daily_rankings("US", "ios", chart_type="free")

    assert written == 1
    # board='free' 透传给抓取层
    assert m.await_args.kwargs["board"] == "free"
    assert await _count("grossing") == 1, "收入榜行不被免费榜同步动到"
    assert await _count("free") == 1, "免费榜行独立落库"


@pytest.mark.asyncio
async def test_free_sync_idempotent_replace(client):
    """免费榜重复同步只替换 free 行，grossing 行毫发无损。"""
    from app import scheduler
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.game import GameRanking, CHART_GROSSING
    today = utcnow_naive().strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        db.add(GameRanking(app_id="com.g.only", date=today, rank=5, country="US",
                           platform="ios", name="收入款", publisher="厂",
                           chart_type=CHART_GROSSING))
        await db.commit()

    fresh = [{"app_id": "com.f.a", "rank": 1, "name": "免费A", "publisher": "厂"},
             {"app_id": "com.f.b", "rank": 2, "name": "免费B", "publisher": "厂"}]
    with patch.object(scheduler.sensor_tower_service, "get_all_rankings_today",
                      new=AsyncMock(return_value=fresh)):
        await scheduler.sync_daily_rankings("US", "ios", chart_type="free")
        await scheduler.sync_daily_rankings("US", "ios", chart_type="free")  # 重跑

    assert await _count("free") == 2, "重跑后仍是 2 条（替换非追加）"
    assert await _count("grossing") == 1, "收入榜行不受影响"


@pytest.mark.asyncio
async def test_grossing_reads_ignore_free_rows(client):
    """零回归：今日榜读路径只返回 grossing 行，free-only 的 app 不出现。"""
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.game import GameRanking, CHART_GROSSING, CHART_FREE
    today = utcnow_naive().strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        db.add(GameRanking(app_id="com.gross.top", date=today, rank=1, country="DE",
                           platform="ios", name="收入榜冠军", publisher="厂",
                           chart_type=CHART_GROSSING))
        db.add(GameRanking(app_id="com.free.only", date=today, rank=1, country="DE",
                           platform="ios", name="只在免费榜", publisher="厂",
                           chart_type=CHART_FREE))
        await db.commit()

    rows = (await client.get("/api/games/rankings?country=DE&platform=ios")).json()
    ids = {r["app_id"] for r in rows}
    assert "com.gross.top" in ids
    assert "com.free.only" not in ids, "免费榜行不得污染今日榜（grossing 口径）"


@pytest.mark.asyncio
async def test_newcomer_detection_ignores_free_rows(client):
    """零回归：detect_newcomers 默认 grossing baseline，free-only 新品不被它检出。"""
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.game import GameRanking, CHART_GROSSING, CHART_FREE
    from app.services.newcomers import detect_newcomers
    now = utcnow_naive()
    today = now.strftime("%Y-%m-%d")
    prev = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        # grossing baseline 锚点
        for d in (prev, today):
            db.add(GameRanking(app_id="anc", date=d, rank=1, country="KR",
                               platform="ios", name="锚", publisher="厂",
                               chart_type=CHART_GROSSING))
        # 一个只在免费榜首发的 app（grossing 口径里根本不存在）
        db.add(GameRanking(app_id="freebie", date=today, rank=2, country="KR",
                           platform="ios", name="免费榜新品", publisher="厂",
                           chart_type=CHART_FREE))
        await db.commit()

    s = await detect_newcomers("KR", "ios", topn=100)
    ids = {n["app_id"] for n in s.get("newcomers") or []}
    assert "freebie" not in ids, "免费榜首发不应被 grossing 口径的检测捞到（切片 2 才接）"


@pytest.mark.asyncio
async def test_scheduled_sync_triggers_free_only_for_configured_combos(client, monkeypatch):
    """_scheduled_sync 只对 FREE_CHART_COMBOS 内的 combo 额外采免费榜。"""
    from app import scheduler
    calls = []

    async def fake_sync(country, platform, with_sales=True, chart_type="grossing"):
        calls.append((country, platform, chart_type))
        return 5
    monkeypatch.setattr(scheduler, "sync_daily_rankings", fake_sync)
    monkeypatch.setattr(scheduler.settings, "FREE_CHART_COMBOS", "US:ios")

    await scheduler._scheduled_sync("US", "ios")   # 在免费榜名单里
    await scheduler._scheduled_sync("US", "android")  # 不在名单里

    assert ("US", "ios", "free") in calls, "配置内 combo 应额外采免费榜"
    assert ("US", "android", "free") not in calls, "未配置 combo 不采免费榜"
    # 两个 combo 的收入榜都照常采
    assert ("US", "ios", "grossing") in calls and ("US", "android", "grossing") in calls
