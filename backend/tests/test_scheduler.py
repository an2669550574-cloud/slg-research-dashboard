"""sync_daily_rankings 的数据安全防线测试。

核心不变量：抓取异常 / 空响应 / 全脏数据时，**绝不能**把当天已有的行删掉。
否则一次 Sensor Tower 抖动就会让图表静默断档。

注意：所有 app.* 必须在函数内 import —— conftest 的 app 夹具会先清空
sys.modules 再用临时 DB 重新装载，模块顶层 import 会绑到旧 DB 上。
"""
import logging
import pytest
from datetime import date, datetime, timedelta
from unittest.mock import patch, AsyncMock


class _FakeSched:
    """替掉模块级 APScheduler 实例，隔离真实线程/事件循环副作用。"""
    running = False

    def __init__(self):
        self.started = False

    def start(self):
        self.started = True

    def add_job(self, *a, **k):
        pass

    def get_jobs(self):
        return []


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


@pytest.mark.asyncio
async def test_start_scheduler_raises_on_empty_combos_real_data(client, monkeypatch):
    """真实数据部署 + 0 同步组合 → 启动即拒绝，不静默裸奔。"""
    from app import scheduler
    from app.config import settings

    monkeypatch.setattr(scheduler, "scheduler", _FakeSched())
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "")   # → sync_combos_list == []
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)

    with pytest.raises(RuntimeError, match="0 valid combos"):
        scheduler.start_scheduler()


# ── 配额分级 cadence（纯函数 + _scheduled_sync 门控）────────────────

def test_due_by_interval_every_day():
    """interval<=1 → 永远到点（每天同步，等于不分级）。"""
    from app.scheduler import _due_by_interval
    d = date(2026, 6, 1)
    assert _due_by_interval(d, 1) is True
    assert _due_by_interval(d, 0) is True  # 防御：非法 0 当每天处理


def test_due_by_interval_alternates_every_other_day():
    """interval=2 → 连续四天 True/False 严格交替。"""
    from app.scheduler import _due_by_interval
    d0 = date(2026, 6, 1)
    res = [_due_by_interval(d0 + timedelta(days=i), 2) for i in range(4)]
    assert res == [res[0], not res[0], res[0], not res[0]]


def test_due_by_interval_weekly_once_in_seven():
    """interval=7 → 任意连续 7 天内恰好一天到点。"""
    from app.scheduler import _due_by_interval
    d0 = date(2026, 6, 1)
    week = [_due_by_interval(d0 + timedelta(days=i), 7) for i in range(7)]
    assert sum(week) == 1


def test_combo_due_primary_and_secondary_use_own_intervals(monkeypatch):
    """主市场走 PRIMARY 间隔、次市场走 SECONDARY 间隔，各自独立。"""
    from app import scheduler
    from app.config import settings
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios,JP:ios")
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS_PRIMARY", "US:ios")
    monkeypatch.setattr(settings, "SYNC_PRIMARY_INTERVAL_DAYS", 1)     # 主市场每天
    monkeypatch.setattr(settings, "SYNC_SECONDARY_INTERVAL_DAYS", 999)  # 次市场几乎不
    days = [date(2026, 6, 1) + timedelta(days=i) for i in range(5)]
    assert all(scheduler._combo_due_today("US", "ios", d) for d in days)
    assert not all(scheduler._combo_due_today("JP", "ios", d) for d in days)


def _date_with_parity(interval: int, due: bool) -> date:
    """找一个对 interval 取模"到点/不到点"符合 due 的日期，避免硬编码 ordinal。"""
    d = date(2026, 6, 1)
    for _ in range(interval + 1):
        if (d.toordinal() % interval == 0) == due:
            return d
        d += timedelta(days=1)
    raise AssertionError("no date found")  # pragma: no cover


@pytest.mark.asyncio
async def test_scheduled_sync_skips_secondary_when_not_due(monkeypatch):
    """次市场非到点日：_scheduled_sync 整轮跳过，不调 sync_daily_rankings（零配额）。"""
    from app import scheduler
    from app.config import settings
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios,JP:ios")
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS_PRIMARY", "US:ios")
    monkeypatch.setattr(settings, "SYNC_SECONDARY_INTERVAL_DAYS", 2)
    not_due = _date_with_parity(2, due=False)
    monkeypatch.setattr(scheduler, "utcnow_naive",
                        lambda: datetime(not_due.year, not_due.month, not_due.day))
    sync_mock = AsyncMock(return_value=5)
    monkeypatch.setattr(scheduler, "sync_daily_rankings", sync_mock)

    await scheduler._scheduled_sync("JP", "ios")
    sync_mock.assert_not_called()


@pytest.mark.asyncio
async def test_scheduled_sync_primary_passes_sales_flag(monkeypatch):
    """主市场：销量非抓取日 → sync_daily_rankings 收到 with_sales=False。"""
    from app import scheduler
    from app.config import settings
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios")
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS_PRIMARY", "US:ios")
    monkeypatch.setattr(settings, "SYNC_PRIMARY_INTERVAL_DAYS", 1)  # 主市场每天到点
    monkeypatch.setattr(settings, "SALES_FETCH_INTERVAL_DAYS", 7)
    monkeypatch.setattr(settings, "USE_MOCK_DATA", True)  # 跳过异动检测
    not_sales = _date_with_parity(7, due=False)
    monkeypatch.setattr(scheduler, "utcnow_naive",
                        lambda: datetime(not_sales.year, not_sales.month, not_sales.day))
    sync_mock = AsyncMock(return_value=3)
    monkeypatch.setattr(scheduler, "sync_daily_rankings", sync_mock)

    await scheduler._scheduled_sync("US", "ios")
    sync_mock.assert_awaited_once()
    assert sync_mock.await_args.kwargs["with_sales"] is False


@pytest.mark.asyncio
async def test_scheduled_sync_secondary_never_fetches_sales(monkeypatch):
    """次市场即便恰逢销量到点日也不抓销量：with_sales 恒 False（JP/KR 销量走详情页按需）。"""
    from app import scheduler
    from app.config import settings
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios,JP:ios")
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS_PRIMARY", "US:ios")
    monkeypatch.setattr(settings, "SYNC_SECONDARY_INTERVAL_DAYS", 1)  # JP 每天到点（不跳过）
    monkeypatch.setattr(settings, "SALES_FETCH_INTERVAL_DAYS", 1)     # 销量每天到点
    monkeypatch.setattr(settings, "USE_MOCK_DATA", True)              # 跳过异动检测
    sync_mock = AsyncMock(return_value=4)
    monkeypatch.setattr(scheduler, "sync_daily_rankings", sync_mock)

    await scheduler._scheduled_sync("JP", "ios")
    sync_mock.assert_awaited_once()
    assert sync_mock.await_args.kwargs["with_sales"] is False, "次市场不该抓销量"


@pytest.mark.asyncio
async def test_sync_daily_rankings_forwards_with_sales(client):
    """sync_daily_rankings(with_sales=False) → get_all_rankings_today 收到同值。"""
    from app import scheduler
    spy = AsyncMock(return_value=[{"app_id": "a", "rank": 1}])
    with patch.object(scheduler.sensor_tower_service, "get_all_rankings_today", new=spy):
        await scheduler.sync_daily_rankings("US", "ios", with_sales=False)
    assert spy.await_args.kwargs["with_sales"] is False


@pytest.mark.asyncio
async def test_start_scheduler_warns_on_empty_combos_mock(client, monkeypatch, caplog):
    """mock 模式下 0 组合只告警、照常启动，不拦开发。"""
    from app import scheduler
    from app.config import settings

    fake = _FakeSched()
    monkeypatch.setattr(scheduler, "scheduler", fake)
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "")
    monkeypatch.setattr(settings, "USE_MOCK_DATA", True)

    with caplog.at_level(logging.WARNING, logger="app.scheduler"):
        scheduler.start_scheduler()  # 不得抛

    assert fake.started is True
    assert any("0 valid combos" in r.getMessage() for r in caplog.records)
