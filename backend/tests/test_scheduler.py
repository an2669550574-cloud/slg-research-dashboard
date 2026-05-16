"""sync_daily_rankings 的数据安全防线测试。

核心不变量：抓取异常 / 空响应 / 全脏数据时，**绝不能**把当天已有的行删掉。
否则一次 Sensor Tower 抖动就会让图表静默断档。

注意：所有 app.* 必须在函数内 import —— conftest 的 app 夹具会先清空
sys.modules 再用临时 DB 重新装载，模块顶层 import 会绑到旧 DB 上。
"""
import pytest
from unittest.mock import patch, AsyncMock


async def _seed_today_row(country="US", platform="ios"):
    """插一行"昨天成功同步留下的"当天数据，作为不该被误删的保护对象。"""
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.game import GameRanking
    today = utcnow_naive().strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        db.add(GameRanking(
            app_id="com.existing.game", date=today, rank=1,
            downloads=1000, revenue=5000, country=country, platform=platform,
            name="Existing Game", publisher="Acme", icon_url=None,
        ))
        await db.commit()


async def _count_today(country="US", platform="ios") -> int:
    from sqlalchemy import select, func
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.game import GameRanking
    today = utcnow_naive().strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(func.count()).select_from(GameRanking).where(
                GameRanking.date == today,
                GameRanking.country == country,
                GameRanking.platform == platform,
            )
        )
        return result.scalar_one()


@pytest.mark.asyncio
async def test_fetch_exception_keeps_existing_rows(client):
    """抓取抛异常时返回 0 且当天旧行原样保留（不执行 DELETE）。"""
    from app import scheduler
    await _seed_today_row()

    with patch.object(
        scheduler.sensor_tower_service, "get_all_rankings_today",
        new=AsyncMock(side_effect=RuntimeError("Sensor Tower 503")),
    ):
        written = await scheduler.sync_daily_rankings("US", "ios")

    assert written == 0
    assert await _count_today() == 1, "异常不该删掉已有数据"


@pytest.mark.asyncio
async def test_empty_response_keeps_existing_rows(client):
    """API 返回空列表时返回 0 且不执行破坏性重写。"""
    from app import scheduler
    await _seed_today_row()

    with patch.object(
        scheduler.sensor_tower_service, "get_all_rankings_today",
        new=AsyncMock(return_value=[]),
    ):
        written = await scheduler.sync_daily_rankings("US", "ios")

    assert written == 0
    assert await _count_today() == 1, "空响应不该删掉已有数据"


@pytest.mark.asyncio
async def test_all_invalid_items_rollback_keeps_existing_rows(client):
    """非空但每条都缺 app_id：DELETE 已发出也要回滚，旧行不丢。"""
    from app import scheduler
    await _seed_today_row()

    with patch.object(
        scheduler.sensor_tower_service, "get_all_rankings_today",
        new=AsyncMock(return_value=[{"rank": 1}, {"rank": 2}]),
    ):
        written = await scheduler.sync_daily_rankings("US", "ios")

    assert written == 0
    assert await _count_today() == 1, "全脏数据应回滚，保留旧行"


@pytest.mark.asyncio
async def test_valid_response_replaces_rows(client):
    """拿到真实非空数据时执行幂等替换：旧行被新行取代。"""
    from app import scheduler
    await _seed_today_row()

    fresh = [
        {"app_id": "com.new.a", "rank": 1, "downloads": 9, "revenue": 99, "name": "A"},
        {"app_id": "com.new.b", "rank": 2, "downloads": 8, "revenue": 88, "name": "B"},
    ]
    with patch.object(
        scheduler.sensor_tower_service, "get_all_rankings_today",
        new=AsyncMock(return_value=fresh),
    ):
        written = await scheduler.sync_daily_rankings("US", "ios")

    assert written == 2
    assert await _count_today() == 2, "应替换为 2 条新数据（旧的那条被覆盖）"
