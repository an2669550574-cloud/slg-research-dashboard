"""下载/免费榜 chart_type 维度（ADR 0001，切片 1）。

核心不变量：
- 收入榜(grossing)与下载榜(free)在同 (app_id,date,country,platform) 并存不撞五元组唯一约束。
- chart_type='free' 同步走 board='free' 拉榜、落 chart_type='free' 行。
- **零回归**：所有现有读路径（今日榜 / 新品检测）只看 grossing，free 行不可见。
"""
import importlib
import pytest
from datetime import timedelta
from unittest.mock import patch, AsyncMock


def _live(mod):
    """conftest 每 test 清 sys.modules——用 importlib 取绑到临时 DB 的活模块。"""
    return importlib.import_module(mod)


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


# ── 切片 2：免费榜进检测/日志/digest ──────────────────────────────

@pytest.mark.asyncio
async def test_record_logs_free_chart_separately(client, monkeypatch):
    """开了下载榜的 combo：record_market_newcomers 两榜各检出各落库（chart_type 区分）。
    非 SLG 的下载榜新品也入库（看板可见）——is_slg 门控只作用于钉钉推送，不拦落库。"""
    nl = importlib.import_module("app.services.newcomer_log")
    database = _live("app.database")
    GameRanking = _live("app.models.game").GameRanking
    CHART_GROSSING = _live("app.models.game").CHART_GROSSING
    CHART_FREE = _live("app.models.game").CHART_FREE
    now = database.utcnow_naive()
    today = now.strftime("%Y-%m-%d")
    prev = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    async with database.AsyncSessionLocal() as db:
        for ct in (CHART_GROSSING, CHART_FREE):  # 两榜各自的 baseline 锚点
            for d in (prev, today):
                db.add(GameRanking(app_id=f"anc_{ct}", date=d, rank=1, country="SG",
                                   platform="ios", name="锚", publisher="厂", chart_type=ct))
        # 收入榜首发 + 下载榜首发（不同 app）
        db.add(GameRanking(app_id="g_new", date=today, rank=3, country="SG",
                           platform="ios", name="收入榜新品", publisher="厂", chart_type=CHART_GROSSING))
        db.add(GameRanking(app_id="f_new", date=today, rank=2, country="SG",
                           platform="ios", name="下载榜新品", publisher="厂", chart_type=CHART_FREE))
        await db.commit()

    monkeypatch.setattr(nl.settings, "USE_MOCK_DATA", True)  # 跳富化
    monkeypatch.setattr(nl, "_POLITE_DELAY_S", 0)
    # free_chart_combos_set 与 sync_combos_list 取交集，故两者都要含 SG:ios
    monkeypatch.setattr(nl.settings, "SYNC_RANKING_COMBOS", "SG:ios")
    monkeypatch.setattr(nl.settings, "FREE_CHART_COMBOS", "SG:ios")
    await nl.record_market_newcomers("SG", "ios")

    # 默认 /history 只看收入榜
    g = (await client.get("/api/newcomers/history?days=7&country=SG")).json()["items"]
    assert {i["app_id"] for i in g} == {"g_new"}
    assert all(i["chart_type"] == "grossing" for i in g)
    # chart=free 看下载榜
    f = (await client.get("/api/newcomers/history?days=7&country=SG&chart=free")).json()["items"]
    assert {i["app_id"] for i in f} == {"f_new"}
    assert f[0]["chart_type"] == "free"
    # chart=all 两榜都返回
    allrows = (await client.get("/api/newcomers/history?days=7&country=SG&chart=all")).json()["items"]
    assert {i["app_id"] for i in allrows} == {"g_new", "f_new"}


def test_build_free_newcomer_lines_slg_gate():
    """下载榜 digest 行只渲染 is_slg=True；非 SLG 被挡（不进钉钉）。"""
    from app.services.release_alerts import build_free_newcomer_lines
    market = {"newcomers": [
        {"app_id": "s1", "rank": 4, "name": "SLG下载新品", "publisher": "厂", "is_slg": True},
        {"app_id": "n1", "rank": 5, "name": "休闲噪声", "publisher": "厂", "is_slg": False},
    ]}
    lines = build_free_newcomer_lines(market, {})
    joined = "\n".join(lines)
    assert "SLG下载新品" in joined and "⬇️" in joined
    assert "休闲噪声" not in joined, "非 SLG 下载榜新品不进钉钉"


def test_daily_digest_renders_free_section():
    """digest 卡片含【下载榜新品 · SLG】段。"""
    from app.services.release_alerts import build_daily_digest
    per_combo = [{
        "country": "US", "platform": "ios", "movement": None, "market": None,
        "publisher": None, "enrich": None,
        "free_market": {"newcomers": [
            {"app_id": "s1", "rank": 2, "name": "下载榜SLG", "publisher": "厂", "is_slg": True}]},
        "free_publisher": None,
    }]
    out = build_daily_digest(per_combo, "2026-06-25")
    assert out is not None
    _, text, _ = out
    assert "【下载榜新品 · SLG】" in text and "下载榜SLG" in text
