"""定时 job 心跳（P1②，补静默失败盲区）：record / 超期自检 / digest 集成。

语义要点：**有过成功记录却超期**才报（治「本来在跑、悄悄停了」）；从没记录 / 不在 specs 的
job 不误报（bootstrap 安全）。所有 app.* 函数内 import（conftest 临时 DB 纪律）。
"""
from datetime import datetime, timedelta

import pytest


async def _set_heartbeat(job_name, ago_hours, note=None):
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.digest import JobHeartbeat
    async with AsyncSessionLocal() as db:
        db.add(JobHeartbeat(job_name=job_name, note=note,
                            last_ok_at=utcnow_naive() - timedelta(hours=ago_hours)))
        await db.commit()


@pytest.mark.asyncio
async def test_record_heartbeat_upsert_and_fresh_not_stale(client):
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.digest import JobHeartbeat
    from app.services.job_heartbeat import record_heartbeat, get_stale_jobs

    await record_heartbeat("daily_rankings_sync", note="US/ios 100 rows")
    async with AsyncSessionLocal() as db:
        row = await db.get(JobHeartbeat, "daily_rankings_sync")
    assert row is not None and row.note == "US/ios 100 rows"
    assert await get_stale_jobs() == []                       # 刚记 → 不 stale

    await record_heartbeat("daily_rankings_sync", note="US/ios 200 rows")  # 二次 → upsert
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(JobHeartbeat))).scalars().all()
    assert len(rows) == 1 and rows[0].note == "US/ios 200 rows"  # 不重复行、note 更新


@pytest.mark.asyncio
async def test_stale_detection_flags_only_expired(client):
    from app.services.job_heartbeat import get_stale_jobs
    await _set_heartbeat("daily_rankings_sync", ago_hours=72)   # max 48 → stale
    await _set_heartbeat("itunes_releases_sync", ago_hours=1)   # fresh
    stale = await get_stale_jobs()
    assert [s["job_name"] for s in stale] == ["daily_rankings_sync"]
    assert stale[0]["label"] == "榜单同步"


@pytest.mark.asyncio
async def test_missing_row_not_stale_bootstrap_safe(client):
    """从没记录的 job（新加 / 没跑过）不误报——首部署不刷屏。"""
    from app.services.job_heartbeat import get_stale_jobs
    assert await get_stale_jobs() == []


@pytest.mark.asyncio
async def test_unregistered_job_ignored(client):
    from app.services.job_heartbeat import get_stale_jobs
    await _set_heartbeat("some_unknown_job", ago_hours=99999)
    assert await get_stale_jobs() == []                         # 不在 specs → 忽略


def test_render_stale_alert():
    from app.services.job_heartbeat import render_stale_alert
    assert render_stale_alert([]) is None
    md = render_stale_alert([{
        "job_name": "daily_rankings_sync", "label": "榜单同步",
        "last_ok_at": datetime(2026, 7, 10, 3, 0), "age_hours": 72, "max_age_hours": 48}])
    assert "任务自检" in md and "榜单同步" in md and "3.0 天" in md
    assert "\n---" not in md  # 无前导分隔（调用方按场景加壳）


@pytest.mark.asyncio
async def test_digest_surfaces_stale_alert_to_maintainer(client, monkeypatch):
    """超期心跳 → 维护者卡出现「任务自检」（append 到卡 或 平淡日单独告警卡，两路皆可）。"""
    import importlib
    ra = importlib.import_module("app.services.release_alerts")
    dt = importlib.import_module("app.services.dingtalk")
    from app.config import settings
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.game import GameRanking

    today = utcnow_naive().strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:  # 一条今日行，让 combo 有数据可跑
        db.add(GameRanking(app_id="rookie", date=today, rank=4, country="US", platform="ios",
                           name="rookie", publisher="Mystery Studio"))
        await db.commit()
    await _set_heartbeat("daily_rankings_sync", ago_hours=100)   # 超期

    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios")
    sent = []

    async def fake_send(title, text, btns=None, **kw):
        sent.append(text)
        return True

    monkeypatch.setattr(dt, "send_action_card", fake_send)
    monkeypatch.setattr(dt, "send_markdown", fake_send)

    await ra.send_daily_digest()
    joined = "\n".join(sent)
    assert "任务自检" in joined and "榜单同步" in joined
