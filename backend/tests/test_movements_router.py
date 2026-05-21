"""/api/movements/ —— 仪表盘今日大事数据源。

核心验证：
- 拉取**不**发 Sentry（与 _scheduled_sync 那条路径解耦）
- 跨 SYNC_RANKING_COMBOS 汇总
- 排序：new_entrant 优先 → drop → surge → revenue_spike，二级键是强度
"""
import logging
import pytest


SLG_PUB = "Century Games Pte. Ltd."


async def _seed(date, rows, country="US", platform="ios"):
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking
    async with AsyncSessionLocal() as db:
        for app_id, rank, revenue, pub in rows:
            db.add(GameRanking(
                app_id=app_id, date=date, rank=rank, downloads=None,
                revenue=revenue, country=country, platform=platform,
                name=app_id, publisher=pub, icon_url=None,
            ))
        await db.commit()


@pytest.mark.asyncio
async def test_movements_returns_flattened_events_for_single_combo(client, caplog):
    """单 combo 查询：今日 vs 昨日异动打平成事件列表。"""
    from app.database import utcnow_naive
    today = utcnow_naive().strftime("%Y-%m-%d")
    # 昨日：仅 'staying' 在 Top；今日：'staying' 大涨 + 'newbie' 新进
    await _seed("2026-04-01", [("staying", 18, 100_000.0, SLG_PUB)])
    await _seed(today, [
        ("staying", 3, 200_000.0, SLG_PUB),  # 18→3 升 + 收入 +100%
        ("newbie",  5, None, SLG_PUB),       # 新进 Top
    ])

    with caplog.at_level(logging.ERROR, logger="app.services.movement"):
        r = await client.get("/api/movements/", params={"country": "US", "platform": "ios"})

    assert r.status_code == 200
    body = r.json()
    assert body["today"] == today
    kinds = [e["kind"] for e in body["events"]]
    # new_entrant 排第一（优先级最高）
    assert kinds[0] == "new_entrant"
    # 应当含 surge 与 revenue_spike
    assert "surge" in kinds and "revenue_spike" in kinds

    # 关键：API 路径**不**该走告警
    errs = [r for r in caplog.records if r.levelno >= logging.ERROR
            and "[COMPETITOR-MOVEMENT]" in r.getMessage()]
    assert errs == [], "API 拉取不能给 Sentry 刷消息"


@pytest.mark.asyncio
async def test_movements_aggregates_across_configured_combos(client, monkeypatch):
    """不传 country/platform → 走 SYNC_RANKING_COMBOS 全集汇总。"""
    from app.database import utcnow_naive
    from app.config import settings
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios,JP:ios")

    today = utcnow_naive().strftime("%Y-%m-%d")
    # 各 combo 各自有一条新进事件
    await _seed("2026-04-01", [("ux", 1, None, SLG_PUB)], country="US", platform="ios")
    await _seed(today, [
        ("ux", 1, None, SLG_PUB),
        ("us_new", 5, None, SLG_PUB),
    ], country="US", platform="ios")

    await _seed("2026-04-01", [("jx", 1, None, SLG_PUB)], country="JP", platform="ios")
    await _seed(today, [
        ("jx", 1, None, SLG_PUB),
        ("jp_new", 7, None, SLG_PUB),
    ], country="JP", platform="ios")

    r = await client.get("/api/movements/")
    body = r.json()
    names = {e["name"] for e in body["events"] if e["kind"] == "new_entrant"}
    assert names == {"us_new", "jp_new"}


@pytest.mark.asyncio
async def test_movements_reports_combos_without_baseline(client, monkeypatch):
    """没有可比对历史日的 combo 进 combos_without_baseline，不抛错。"""
    from app.database import utcnow_naive
    from app.config import settings
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios,JP:ios")

    today = utcnow_naive().strftime("%Y-%m-%d")
    # 只给 US/ios 有历史；JP/ios 是冷库
    await _seed("2026-04-01", [("a", 1, None, SLG_PUB)], country="US", platform="ios")
    await _seed(today, [("a", 1, None, SLG_PUB)], country="US", platform="ios")

    r = await client.get("/api/movements/")
    body = r.json()
    assert "JP/ios" in body["combos_without_baseline"]
    assert "US/ios" not in body["combos_without_baseline"]


@pytest.mark.asyncio
async def test_movements_empty_returns_200_with_empty_list(client, monkeypatch):
    """同步过但无 SLG 异动 → 空 events，仍 200，前端可展示"今日无显著异动"。"""
    from app.database import utcnow_naive
    from app.config import settings
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios")

    today = utcnow_naive().strftime("%Y-%m-%d")
    await _seed("2026-04-01", [("steady", 5, 100_000.0, SLG_PUB)])
    # 今日完全没动
    await _seed(today, [("steady", 5, 100_000.0, SLG_PUB)])

    r = await client.get("/api/movements/")
    assert r.status_code == 200
    assert r.json()["events"] == []
