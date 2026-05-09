"""Sensor Tower 月度配额 + 快照存储 + /api/quota 接口的集成测试。"""
import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_quota_starts_at_zero(client):
    resp = await client.get("/api/quota/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["used"] == 0
    assert body["limit"] == 500
    assert body["remaining"] == 500
    assert body["exhausted"] is False
    assert len(body["year_month"]) == 7  # "YYYY-MM"


@pytest.mark.asyncio
async def test_try_consume_increments_and_blocks_at_limit(client):
    """前 limit 次返回 True 并累加；超过后返回 False，且 count 不会涨到 limit+1。"""
    from app.services import quota
    from app.config import settings

    # 把 limit 调小一点跑得快
    with patch.object(settings, "SENSOR_TOWER_MONTHLY_LIMIT", 3):
        for i in range(3):
            assert await quota.try_consume() is True
        # 第 4 次应该被拒绝
        assert await quota.try_consume() is False

        usage = await quota.current_usage()
        assert usage["used"] == 3, "拒绝调用不应该让 count 超过 limit"
        assert usage["exhausted"] is True


@pytest.mark.asyncio
async def test_snapshot_round_trip(client):
    from app.services import quota

    payload = {"apps": [{"app_id": "com.test.x", "rank": 1}]}
    await quota.save_snapshot("test_key", payload)

    loaded = await quota.load_snapshot("test_key")
    assert loaded == payload

    # 覆盖写
    new_payload = {"apps": [{"app_id": "com.test.x", "rank": 2}]}
    await quota.save_snapshot("test_key", new_payload)
    assert await quota.load_snapshot("test_key") == new_payload

    # 不存在的 key
    assert await quota.load_snapshot("missing") is None


@pytest.mark.asyncio
async def test_load_snapshot_if_fresh(client):
    """新鲜窗口内的快照命中；过期则不命中。"""
    from app.services import quota
    from sqlalchemy import text
    from app.database import AsyncSessionLocal

    payload = {"apps": [{"rank": 1}]}
    await quota.save_snapshot("fresh_key", payload)

    # 1 小时内的快照在 24h 窗口内应命中
    assert await quota.load_snapshot_if_fresh("fresh_key", max_age_seconds=86400) == payload

    # 把 updated_at 强行回退 25 小时，模拟过期
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE sensor_tower_snapshots "
                "SET updated_at = datetime('now', '-25 hours') "
                "WHERE cache_key = 'fresh_key'"
            )
        )
        await session.commit()

    # 24h 窗口下应失效，但 load_snapshot 不带过期检查仍能拿到
    assert await quota.load_snapshot_if_fresh("fresh_key", max_age_seconds=86400) is None
    assert await quota.load_snapshot("fresh_key") == payload

    # 不存在的 key 也返回 None
    assert await quota.load_snapshot_if_fresh("missing", max_age_seconds=86400) is None


@pytest.mark.asyncio
async def test_month_boundary_separate_counters(client):
    """模拟跨月：不同的 year_month 字符串各自计数互不干扰。"""
    from app.services import quota

    # 直接走 _consume_in 拿 session 控制 ym
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        # 上个月已经用了 5 次
        for _ in range(5):
            await quota._consume_in(session, "2026-04", limit=500)
        # 这个月单独计数
        for _ in range(2):
            await quota._consume_in(session, "2026-05", limit=500)

        from sqlalchemy import text
        result = await session.execute(
            text("SELECT year_month, count FROM api_quota_monthly ORDER BY year_month")
        )
        rows = result.all()
        assert rows == [("2026-04", 5), ("2026-05", 2)]
