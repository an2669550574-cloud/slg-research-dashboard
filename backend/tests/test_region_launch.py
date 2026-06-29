"""tracked iOS 竞品分地区上架日（需求② 子项③ / ADR 0004）。

验收：分地区 releaseDate 落库 / trackId（ios_track_id 优先）映射回业务 app_id /
upsert 刷新（不新增重复行）/ 该区查不到留 NULL / mock + 无可用 trackId no-op /
端点按上架日升序（NULL 沉底）。中文游戏名（CJK 硬规则）。
"""
import pytest
from sqlalchemy import select


async def _add_game(app_id, name, platform="ios", ios_track_id=None):
    from app.database import AsyncSessionLocal
    from app.models.game import Game
    async with AsyncSessionLocal() as db:
        db.add(Game(app_id=app_id, name=name, platform=platform,
                    ios_track_id=ios_track_id))
        await db.commit()


@pytest.mark.asyncio
async def test_sync_fills_per_region_dates(app, monkeypatch):
    """每 storefront 落一行 releaseDate；ios_track_id 映射回业务 app_id。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.game import GameRegionRelease
    from app.services import region_launch as rl
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)

    # 按 country 返回不同 releaseDate（德区早于美区 = soft-launch 区序）。
    dates = {"us": "2023-02-12", "de": "2022-12-02"}

    async def fake_bulk(ids, country="us"):
        return {"999": {"release_date": dates.get(country)}}
    monkeypatch.setattr(rl, "fetch_apps_bulk", fake_bulk)

    await _add_game("com.gp.whiteout", "白幕求生", ios_track_id="999")
    out = await rl.sync_region_launches(storefronts=["us", "de"])
    assert out == {"games": 1, "storefronts": 2, "rows": 2}

    async with AsyncSessionLocal() as db:
        rows = {r.country: r for r in
                (await db.execute(select(GameRegionRelease))).scalars().all()}
    assert rows["us"].app_id == "com.gp.whiteout"   # trackId → 业务 app_id
    assert rows["us"].release_date == "2023-02-12"
    assert rows["de"].release_date == "2022-12-02"


@pytest.mark.asyncio
async def test_sync_upsert_refresh_and_null(app, monkeypatch):
    """二次 sync 覆盖既有行（upsert，不新增重复）；该区查不到 → release_date=NULL。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.game import GameRegionRelease
    from app.services import region_launch as rl
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)

    seq = {"v": "2023-01-01"}

    async def fake_bulk(ids, country="us"):
        # v=None 模拟该区 resultCount=0 → bulk 缺该 id。
        return {"999": {"release_date": seq["v"]}} if seq["v"] else {}
    monkeypatch.setattr(rl, "fetch_apps_bulk", fake_bulk)

    await _add_game("app1", "游戏一", ios_track_id="999")
    await rl.sync_region_launches(storefronts=["us"])
    seq["v"] = None
    await rl.sync_region_launches(storefronts=["us"])

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(GameRegionRelease))).scalars().all()
    assert len(rows) == 1            # upsert，不新增重复行
    assert rows[0].release_date is None   # 刷新成 NULL（诚实留空）


@pytest.mark.asyncio
async def test_sync_atomic_upsert_survives_preexisting_row(app, monkeypatch):
    """加固：(app_id,country) 已存在行（模拟并发 job / 手动 sync 已先 INSERT 该对）时，
    走原子 upsert 覆盖、不撞 uq_game_region_release 崩溃。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.game import GameRegionRelease
    from app.services import region_launch as rl
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)

    async def fake_bulk(ids, country="us"):
        return {"999": {"release_date": "2024-05-05"}}
    monkeypatch.setattr(rl, "fetch_apps_bulk", fake_bulk)

    await _add_game("app9", "并发游戏", ios_track_id="999")
    async with AsyncSessionLocal() as db:   # 预置一行 = 模拟并发先落
        db.add(GameRegionRelease(app_id="app9", country="us", release_date="2000-01-01"))
        await db.commit()

    out = await rl.sync_region_launches(storefronts=["us"])   # 不应抛 IntegrityError
    assert out == {"games": 1, "storefronts": 1, "rows": 1}
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(GameRegionRelease))).scalars().all()
    assert len(rows) == 1                        # 仍 1 行（upsert 覆盖、不新增）
    assert rows[0].release_date == "2024-05-05"  # 覆盖成最新值


@pytest.mark.asyncio
async def test_sync_noop_mock_and_no_trackid(app, monkeypatch):
    """USE_MOCK_DATA → no-op；非 mock 但无可用 trackId（GP 包名）→ 直接返回、不打 iTunes。"""
    from app.config import settings
    from app.services import region_launch as rl

    monkeypatch.setattr(settings, "USE_MOCK_DATA", True)
    assert await rl.sync_region_launches(storefronts=["us"]) == {
        "games": 0, "storefronts": 0, "rows": 0}

    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    called = {"hit": False}

    async def fake_bulk(ids, country="us"):
        called["hit"] = True
        return {}
    monkeypatch.setattr(rl, "fetch_apps_bulk", fake_bulk)

    await _add_game("com.gp.nopkg", "无id游戏")   # ios 但无 ios_track_id 且 app_id 非数字
    out = await rl.sync_region_launches(storefronts=["us"])
    assert out == {"games": 0, "storefronts": 0, "rows": 0}
    assert called["hit"] is False    # 无可用 trackId 直接返回，没打 iTunes


@pytest.mark.asyncio
async def test_detect_new_region_only_recent_and_dedups(app, monkeypatch):
    """新区检测：只播报近 N 天上架日的区（历史老日期不算）；落 GameHistory；二次去重。"""
    from datetime import timedelta
    from app.config import settings
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.game import GameRegionRelease
    from app.models.history import GameHistory
    from app.services import region_launch as rl
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)

    recent = (utcnow_naive() - timedelta(days=3)).strftime("%Y-%m-%d")
    old = (utcnow_naive() - timedelta(days=400)).strftime("%Y-%m-%d")
    await _add_game("g1", "万国觉醒")
    async with AsyncSessionLocal() as db:
        db.add(GameRegionRelease(app_id="g1", country="kr", release_date=recent))  # 近期 → 事件
        db.add(GameRegionRelease(app_id="g1", country="us", release_date=old))     # 老 → 不报
        db.add(GameRegionRelease(app_id="g1", country="cn", release_date=None))    # NULL → 不报
        await db.commit()

    changes = await rl.detect_new_region_launches(recent_days=30)
    assert len(changes) == 1
    assert changes[0]["name"] == "万国觉醒" and changes[0]["country"] == "KR"
    async with AsyncSessionLocal() as db:
        evs = (await db.execute(select(GameHistory).where(
            GameHistory.event_type == "region_launch"))).scalars().all()
    assert len(evs) == 1 and "KR" in evs[0].title    # 落 GameHistory，详情页时间线可见

    # 二次检测：已播报过 → 去重、不再产出 / 不重复落库。
    again = await rl.detect_new_region_launches(recent_days=30)
    assert again == []
    async with AsyncSessionLocal() as db:
        n = len((await db.execute(select(GameHistory).where(
            GameHistory.event_type == "region_launch"))).scalars().all())
    assert n == 1


@pytest.mark.asyncio
async def test_region_history_accrues_without_webhook(app, monkeypatch):
    """新区检测排在 webhook 闸门之前：未配 webhook 也落 region_launch 历史（与版本追踪
    同范式，避免事件因 30 天窗口过期而永久漏记）。"""
    from datetime import timedelta
    from app.config import settings
    from app.database import AsyncSessionLocal, utcnow_naive
    from app.models.game import Game, GameRegionRelease
    from app.models.history import GameHistory
    from app.services import release_alerts as ra
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "DINGTALK_WEBHOOK_URL", "")   # webhook 关

    recent = (utcnow_naive() - timedelta(days=2)).strftime("%Y-%m-%d")
    async with AsyncSessionLocal() as db:
        db.add(Game(app_id="g1", name="万国觉醒", platform="ios"))  # 无 trackId → 版本检测跳过
        db.add(GameRegionRelease(app_id="g1", country="kr", release_date=recent))
        await db.commit()

    assert await ra.send_daily_digest() is False    # webhook 关，没发卡
    async with AsyncSessionLocal() as db:
        evs = (await db.execute(select(GameHistory).where(
            GameHistory.event_type == "region_launch"))).scalars().all()
    assert len(evs) == 1 and "KR" in evs[0].title   # 但 region_launch 历史照样落了


def test_digest_renders_region_section():
    """build_daily_digest 把新区上线拼成全局段；纯该类日也发卡。
    （实机视频不再单列整段——已内联进各新品行，见 test_digest_video_inlined_into_newcomer_row。）"""
    from app.services.release_alerts import build_daily_digest
    region = [{"app_id": "g1", "name": "万国觉醒", "country": "KR", "date": "2026-06-25"}]
    res = build_daily_digest([], "2026-06-27", region_changes=region)
    assert res is not None
    _, body, _ = res
    assert "竞品新区上线" in body and "万国觉醒" in body and "新进 KR 区" in body


@pytest.mark.asyncio
async def test_regions_endpoint_orders_earliest_first(client):
    """端点按上架日升序、NULL 沉底（最早区先 = soft-launch 区序一目了然）。"""
    from app.database import AsyncSessionLocal
    from app.models.game import GameRegionRelease
    async with AsyncSessionLocal() as db:
        db.add(GameRegionRelease(app_id="g1", country="us", release_date="2023-02-12"))
        db.add(GameRegionRelease(app_id="g1", country="de", release_date="2022-12-02"))
        db.add(GameRegionRelease(app_id="g1", country="kr", release_date=None))
        await db.commit()

    r = await client.get("/api/games/g1/regions")
    assert r.status_code == 200
    body = r.json()
    assert [v["country"] for v in body] == ["de", "us", "kr"]  # 最早(de)在前，NULL(kr)沉底
