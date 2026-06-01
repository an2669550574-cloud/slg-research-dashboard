"""Sensor Tower 月度配额 + 快照存储 + /api/quota 接口的集成测试。"""
import logging
import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_quota_starts_at_zero(client):
    from app.config import settings
    limit = settings.SENSOR_TOWER_MONTHLY_LIMIT
    resp = await client.get("/api/quota/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["used"] == 0
    assert body["limit"] == limit
    assert body["remaining"] == limit
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


def _quota_errors(caplog):
    return [r for r in caplog.records
            if r.name == "app.services.quota" and r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_quota_alert_edges_fire_once_each(client, caplog):
    """越过告警线、用满耗尽各恰好告警一次；之间与之后都不刷屏。"""
    from app.services import quota
    from app.config import settings

    with patch.object(settings, "SENSOR_TOWER_MONTHLY_LIMIT", 10), \
         patch.object(settings, "SENSOR_TOWER_QUOTA_WARN_PCT", 80):
        with caplog.at_level(logging.ERROR, logger="app.services.quota"):
            # warn_at = ceil(10 * 80 / 100) = 8
            for _ in range(7):
                assert await quota.try_consume() is True
            assert _quota_errors(caplog) == [], "阈值以下应静默"

            assert await quota.try_consume() is True  # 第 8 次 = 告警线
            errs = _quota_errors(caplog)
            assert len(errs) == 1 and "crossed 80%" in errs[0].getMessage()

            assert await quota.try_consume() is True  # 第 9 次：不再重复告警
            assert len(_quota_errors(caplog)) == 1

            assert await quota.try_consume() is True  # 第 10 次 = limit，耗尽边沿
            errs = _quota_errors(caplog)
            assert len(errs) == 2 and "EXHAUSTED" in errs[-1].getMessage()

            assert await quota.try_consume() is False  # 第 11 次被拒，不再告警
            assert len(_quota_errors(caplog)) == 2


@pytest.mark.asyncio
async def test_quota_alert_rearms_next_month(client, caplog):
    """跨月后阈值自动重新武装：每个 year_month 各自再告警一次。"""
    from app.services import quota
    from app.database import AsyncSessionLocal
    from app.config import settings

    with patch.object(settings, "SENSOR_TOWER_QUOTA_WARN_PCT", 80):
        with caplog.at_level(logging.ERROR, logger="app.services.quota"):
            async with AsyncSessionLocal() as session:
                # limit=5 → warn_at = ceil(5*0.8) = 4
                for _ in range(4):
                    await quota._consume_in(session, "2026-07", limit=5)
                for _ in range(4):
                    await quota._consume_in(session, "2026-08", limit=5)
            crossed = [r for r in _quota_errors(caplog) if "crossed 80%" in r.getMessage()]
            assert len(crossed) == 2, "每月各告警一次 = 跨月已重新武装"


@pytest.mark.asyncio
async def test_refund_decrements_and_floors_at_zero(client):
    """退还一次配额；多退不会把 count 弄成负数。"""
    from app.services import quota

    assert await quota.try_consume() is True
    assert (await quota.current_usage())["used"] == 1

    await quota.refund()
    assert (await quota.current_usage())["used"] == 0

    await quota.refund()  # 已是 0，再退仍为 0
    assert (await quota.current_usage())["used"] == 0


@pytest.mark.asyncio
async def test_try_consume_blocked_when_org_pool_in_reserved_band(client):
    """公司池剩余 ≤ ORG_RESERVE 时 try_consume 返 False，且不动本地计数。

    这是"对其他团队负责"的护栏：池子最后 30 次让出去，不要本项目一夜拼光。
    """
    from app.services import quota
    from app.config import settings

    # 灌一份 org pool 几近耗尽的 account_usage 快照
    await quota.save_snapshot(quota.ACCOUNT_USAGE_KEY, {
        "organization": {"usage": 2980, "limit": 3000, "tier": None},
        "user": {"usage": 100},
    })

    with patch.object(settings, "SENSOR_TOWER_ORG_RESERVE", 30):
        # 剩 20 ≤ 30 → 应被 reserve guard 拦下
        assert await quota.try_consume() is False
        # 本地 counter 不应被动到（拒绝路径根本没碰 _consume_in）
        assert (await quota.current_usage())["used"] == 0


@pytest.mark.asyncio
async def test_try_consume_passes_when_org_pool_above_reserve(client):
    """公司池剩余 > ORG_RESERVE 时仍可正常 consume。低/高阈值切换不影响放行。"""
    from app.services import quota
    from app.config import settings

    await quota.save_snapshot(quota.ACCOUNT_USAGE_KEY, {
        "organization": {"usage": 2000, "limit": 3000, "tier": None},
        "user": {"usage": 100},
    })

    with patch.object(settings, "SENSOR_TOWER_ORG_RESERVE", 30):
        assert await quota.try_consume() is True
        assert (await quota.current_usage())["used"] == 1


@pytest.mark.asyncio
async def test_try_consume_passes_when_no_org_snapshot(client):
    """缺账户用量快照（mock/启动初期）时不限流，保守放行。"""
    from app.services import quota

    # 不灌快照
    assert await quota.try_consume() is True


@pytest.mark.asyncio
async def test_classify_state_thresholds(client):
    """边界值：== reserve 算 reserved；== low 算 low；> low 算 normal。"""
    from app.services import quota
    from app.config import settings

    with patch.object(settings, "SENSOR_TOWER_ORG_RESERVE", 30), \
         patch.object(settings, "SENSOR_TOWER_ORG_LOW_THRESHOLD", 100):
        assert quota._classify_state(None) == "normal"
        assert quota._classify_state(101) == "normal"
        assert quota._classify_state(100) == "low"
        assert quota._classify_state(31) == "low"
        assert quota._classify_state(30) == "reserved"
        assert quota._classify_state(0) == "reserved"


@pytest.mark.asyncio
async def test_current_usage_reports_account_state(client):
    """current_usage 暴露 account_state，前端据此决定弹什么色调的全局条。"""
    from app.services import quota
    from app.config import settings

    async def fake_live():
        return {
            "organization": {"usage": 2950, "limit": 3000, "tier": None},
            "user": {"usage": 100},
        }

    with patch.object(quota, "_fetch_account_usage_live", fake_live), \
         patch.object(settings, "SENSOR_TOWER_ORG_RESERVE", 30), \
         patch.object(settings, "SENSOR_TOWER_ORG_LOW_THRESHOLD", 100):
        body = await quota.current_usage()

    assert body["account_state"] == "low"  # 剩 50 ∈ (30, 100]
    assert body["organization"]["remaining"] == 50


@pytest.mark.asyncio
async def test_get_account_usage_mock_mode_returns_none(client):
    """mock 模式 / 无 API key 时不联网，直接返回 None；current_usage() 也带空字段。"""
    from app.services import quota

    # conftest 已把 USE_MOCK_DATA=true、SENSOR_TOWER_API_KEY="" 配好
    assert await quota.get_account_usage() is None

    body = await quota.current_usage()
    assert body["organization"] is None
    assert body["account_user_usage"] is None
    assert body["account_stale"] is None


@pytest.mark.asyncio
async def test_get_account_usage_caches_live_response(client):
    """live 拉成功后写 snapshot；下次 TTL 内调用走缓存（不再打网络）。"""
    from app.services import quota

    fake_payload = {
        "organization": {"usage": 2943, "limit": 3000, "tier": None},
        "user": {"usage": 102},
    }

    call_count = {"n": 0}

    async def fake_live():
        call_count["n"] += 1
        return fake_payload

    with patch.object(quota, "_fetch_account_usage_live", fake_live):
        first = await quota.get_account_usage()
        assert first["organization"]["usage"] == 2943
        assert first["user"]["usage"] == 102
        assert first["stale"] is False
        assert call_count["n"] == 1

        # 第二次应当走 fresh snapshot，不再调 live
        second = await quota.get_account_usage()
        assert second["organization"]["usage"] == 2943
        assert second["stale"] is False
        assert call_count["n"] == 1, "TTL 内重复调用必须走缓存"


@pytest.mark.asyncio
async def test_get_account_usage_falls_back_to_stale_snapshot_on_failure(client):
    """live 拉失败时回退到任何历史快照并标记 stale=True；无快照则返 None。"""
    from app.services import quota
    from sqlalchemy import text
    from app.database import AsyncSessionLocal

    # 无快照 + live 失败 → None
    async def fail_live():
        return None

    with patch.object(quota, "_fetch_account_usage_live", fail_live):
        assert await quota.get_account_usage() is None

    # 灌一个旧快照并把 updated_at 推过 TTL，再让 live 失败 → 拿到 stale=True
    fake = {
        "organization": {"usage": 1500, "limit": 3000, "tier": None},
        "user": {"usage": 50},
    }
    await quota.save_snapshot(quota.ACCOUNT_USAGE_KEY, fake)
    async with AsyncSessionLocal() as s:
        await s.execute(
            text(
                "UPDATE sensor_tower_snapshots SET updated_at = datetime('now', '-30 days') "
                "WHERE cache_key = :k"
            ).bindparams(k=quota.ACCOUNT_USAGE_KEY)
        )
        await s.commit()

    with patch.object(quota, "_fetch_account_usage_live", fail_live):
        result = await quota.get_account_usage()
        assert result is not None
        assert result["organization"]["usage"] == 1500
        assert result["stale"] is True


@pytest.mark.asyncio
async def test_current_usage_exposes_org_block_when_account_available(client):
    """当 account_usage 有数据时，current_usage 暴露 organization 块（含 percentage）。"""
    from app.services import quota

    fake = {
        "organization": {"usage": 2943, "limit": 3000, "tier": None},
        "user": {"usage": 102},
    }

    async def fake_live():
        return fake

    with patch.object(quota, "_fetch_account_usage_live", fake_live):
        body = await quota.current_usage()

    assert body["organization"] == {
        "usage": 2943,
        "limit": 3000,
        "remaining": 57,
        "percentage": 98.1,  # round(2943/3000*100, 1)
        "tier": None,
    }
    assert body["account_user_usage"] == 102
    assert body["account_stale"] is False


@pytest.mark.asyncio
async def test_try_consume_increments_daily_in_lockstep(client):
    """成功 consume 同时增加 monthly + 当天 daily 计数(同事务原子)。"""
    from app.services import quota
    from app.database import AsyncSessionLocal
    from sqlalchemy import text

    for _ in range(3):
        assert await quota.try_consume() is True

    today = quota.current_date_utc()
    async with AsyncSessionLocal() as s:
        r = await s.execute(text("SELECT count FROM api_quota_daily WHERE date = :d").bindparams(d=today))
        row = r.first()

    assert row is not None and row[0] == 3
    assert (await quota.current_usage())["used"] == 3


@pytest.mark.asyncio
async def test_rejected_consume_does_not_touch_daily(client):
    """超过 limit 被拒的调用 monthly 已回滚,daily 也不应被记入。"""
    from app.services import quota
    from app.config import settings
    from app.database import AsyncSessionLocal
    from sqlalchemy import text

    with patch.object(settings, "SENSOR_TOWER_MONTHLY_LIMIT", 2):
        assert await quota.try_consume() is True
        assert await quota.try_consume() is True
        assert await quota.try_consume() is False  # 被拒

    today = quota.current_date_utc()
    async with AsyncSessionLocal() as s:
        r = await s.execute(text("SELECT count FROM api_quota_daily WHERE date = :d").bindparams(d=today))
        assert r.first()[0] == 2, "daily 计数应只反映成功的两次"


@pytest.mark.asyncio
async def test_refund_decrements_daily_too(client):
    """refund() 既扣 monthly 也扣 daily,多退也不会变负。"""
    from app.services import quota
    from app.database import AsyncSessionLocal
    from sqlalchemy import text

    assert await quota.try_consume() is True
    await quota.refund()

    today = quota.current_date_utc()
    async with AsyncSessionLocal() as s:
        r = await s.execute(text("SELECT count FROM api_quota_daily WHERE date = :d").bindparams(d=today))
        assert r.first()[0] == 0

    await quota.refund()  # 已是 0
    async with AsyncSessionLocal() as s:
        r = await s.execute(text("SELECT count FROM api_quota_daily WHERE date = :d").bindparams(d=today))
        assert r.first()[0] == 0


@pytest.mark.asyncio
async def test_usage_history_fills_zero_for_missing_days(client):
    """没记录的日子也要出现在结果里(count=0),折线才不跳过空日。"""
    from app.services import quota
    from app.database import AsyncSessionLocal
    from sqlalchemy import text

    # 灌两个不连续的真实日
    async with AsyncSessionLocal() as s:
        await s.execute(text(
            "INSERT INTO api_quota_daily (date, count, updated_at) VALUES "
            "('2026-05-15', 7, CURRENT_TIMESTAMP), "
            "('2026-05-18', 3, CURRENT_TIMESTAMP)"
        ))
        await s.commit()

    # 当前 UTC 日是 2026-05-21 (test conftest 不冻时间,但 today 取 utcnow_naive)
    # 我们覆盖 utcnow_naive 来锚定窗口
    from datetime import datetime
    import app.services.quota as q
    real_utcnow = q.utcnow_naive
    q.utcnow_naive = lambda: datetime(2026, 5, 21)
    try:
        points = await quota.usage_history(days=7)  # 5/15 .. 5/21
    finally:
        q.utcnow_naive = real_utcnow

    assert [p["date"] for p in points] == [
        "2026-05-15", "2026-05-16", "2026-05-17",
        "2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21",
    ]
    assert [p["count"] for p in points] == [7, 0, 0, 3, 0, 0, 0]


@pytest.mark.asyncio
async def test_history_endpoint_returns_shape(client):
    """/api/quota/history 返回 {days, points}。"""
    resp = await client.get("/api/quota/history?days=14")
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 14
    assert isinstance(body["points"], list)
    assert len(body["points"]) == 14
    for p in body["points"]:
        assert set(p.keys()) == {"date", "count"}
        assert isinstance(p["count"], int)


@pytest.mark.asyncio
async def test_history_endpoint_validates_days_range(client):
    """days 越界返 422(FastAPI 校验);0 或 999 都不接受。"""
    assert (await client.get("/api/quota/history?days=0")).status_code == 422
    assert (await client.get("/api/quota/history?days=999")).status_code == 422


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
