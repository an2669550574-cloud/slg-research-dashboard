"""历史排名涓流回填：配额护栏 / 每日预算上限 / 跳过已补不耗预算 /
合并只写 rank 不毁销量历史。conftest 重载 app.* → 测内 import。
"""
import pytest
from datetime import timedelta
from unittest.mock import AsyncMock


async def _seed(rows):
    """rows: (app_id, date, rank, downloads, revenue, country, platform)。"""
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking
    async with AsyncSessionLocal() as db:
        for aid, d, rk, dl, rv, c, p in rows:
            db.add(GameRanking(app_id=aid, date=d, rank=rk, downloads=dl,
                               revenue=rv, country=c, platform=p))
        await db.commit()


async def _fetch_row(app_id, date, country, platform):
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(GameRanking).where(
            GameRanking.app_id == app_id, GameRanking.date == date,
            GameRanking.country == country, GameRanking.platform == platform))
        return r.scalar_one_or_none()


def _prep(monkeypatch):
    """共用：真实模式 + 2 组合 + 周窗=3 + 每日预算=2。返回 (d1, d2)。"""
    from app.config import settings
    from app.database import utcnow_naive
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "RANK_BACKFILL_ENABLED", True)
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios,US:android")
    monkeypatch.setattr(settings, "RANK_BACKFILL_WEEKS", 3)
    monkeypatch.setattr(settings, "RANK_BACKFILL_DAILY_BUDGET", 2)
    monkeypatch.setattr(settings, "RANK_BACKFILL_QUOTA_FLOOR", 150)
    today = utcnow_naive().date()
    return ((today - timedelta(days=7)).strftime("%Y-%m-%d"),
            (today - timedelta(days=14)).strftime("%Y-%m-%d"))


@pytest.mark.asyncio
async def test_quota_floor_skips_entire_run(client, monkeypatch):
    _prep(monkeypatch)
    from app.services import rank_backfill, quota
    monkeypatch.setattr(quota, "current_usage",
                        AsyncMock(return_value={"remaining": 100}))  # ≤ floor 150
    spy = AsyncMock()
    monkeypatch.setattr(rank_backfill.sensor_tower_service, "get_ranking_on_date", spy)

    written = await rank_backfill.backfill_rank_history()
    assert written == 0
    spy.assert_not_awaited()  # 护栏：一次真实拉取都不该发生


@pytest.mark.asyncio
async def test_budget_cap_skip_done_and_rank_merge_preserves_sales(client, monkeypatch):
    d1, d2 = _prep(monkeypatch)
    from app.services import rank_backfill, quota

    # d1/US/ios：已有"销量回填行"(rank=NULL，带下载/收入) —— 合并后必须留着
    # d1/US/android：已有名次行 → 应跳过且不耗预算
    await _seed([
        ("sales.app", d1, None, 999, 8888.0, "US", "ios"),
        ("done.app",  d1, 7,    None, None,   "US", "android"),
    ])
    monkeypatch.setattr(quota, "current_usage",
                        AsyncMock(return_value={"remaining": 400}))  # 充足
    fake = AsyncMock(return_value=[{"app_id": "sales.app", "rank": 3},
                                   {"app_id": "new.app", "rank": 1}])
    monkeypatch.setattr(rank_backfill.sensor_tower_service, "get_ranking_on_date", fake)

    written = await rank_backfill.backfill_rank_history()

    # 预算=2：d1/US:ios(取#1) → d1/US:android(已补,跳过,不计) →
    # d2/US:ios(取#2) → 预算耗尽，d2/US:android 够不着
    assert fake.await_count == 2
    assert written == 4  # 每次合并 2 行

    # 关键：rank 合并进销量行，downloads/revenue 原样保住，没被清
    s = await _fetch_row("sales.app", d1, "US", "ios")
    assert (s.rank, s.downloads, s.revenue) == (3, 999, 8888.0)
    # 新 app 仅 rank
    n = await _fetch_row("new.app", d1, "US", "ios")
    assert n.rank == 1 and n.downloads is None
    # 已补组合未被二次拉取改动
    dn = await _fetch_row("done.app", d1, "US", "android")
    assert dn.rank == 7
    # 第 2 次配额落在 d2/US:ios
    assert await _fetch_row("new.app", d2, "US", "ios") is not None
    # 预算耗尽前没碰 d2/US:android
    assert await _fetch_row("new.app", d2, "US", "android") is None
