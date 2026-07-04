"""检出后走势 compute_trajectories（读时 join game_rankings，零 ST）。

核心验证：
- climbing / falling / stable：检出名次 vs 最新快照名次的方向判定（阈值 5）
- dropped：检出后掉出采集深度（最新快照里已无它）→ on_chart=False、current_rank=None
- new：检出当天即最新快照（days_tracked=0）→ 暂无后续数据可判走势
- unknown：无任何 game_rankings 点（历史脏行）
- peak_rank / days_tracked / 中文夹具（CJK 纪律）
"""
import importlib

import pytest


def _live(mod):
    """conftest 每 test 清 sys.modules——顶层 import 会拿到过期模块，用 importlib 取活模块。"""
    return importlib.import_module(mod)


async def _seed_ranks(app_id, points, country="US", platform="ios", chart_type="grossing"):
    """points: list of (date, rank)。往 game_rankings 塞该 app 的名次轨迹点。"""
    database = _live("app.database")
    GameRanking = _live("app.models.game").GameRanking
    async with database.AsyncSessionLocal() as db:
        for date, rank in points:
            db.add(GameRanking(
                app_id=app_id, date=date, rank=rank, country=country, platform=platform,
                chart_type=chart_type, name=f"游戏{app_id}", publisher="神秘工作室"))
        await db.commit()


async def _seed_log(app_id, as_of, rank, country="US", platform="ios", chart_type="grossing"):
    """往 market_newcomer_log 塞一条检出行（compute_trajectories 的输入）。"""
    database = _live("app.database")
    MarketNewcomerLog = _live("app.models.newcomer").MarketNewcomerLog
    async with database.AsyncSessionLocal() as db:
        db.add(MarketNewcomerLog(
            country=country, platform=platform, app_id=app_id, chart_type=chart_type,
            as_of=as_of, rank=rank, name=f"游戏{app_id}", publisher="神秘工作室"))
        await db.commit()


async def _run():
    """查全部检出行、算走势，返回 {app_id: trajectory_dict}。"""
    database = _live("app.database")
    MarketNewcomerLog = _live("app.models.newcomer").MarketNewcomerLog
    compute_trajectories = _live("app.services.newcomers").compute_trajectories
    from sqlalchemy import select
    async with database.AsyncSessionLocal() as db:
        rows = (await db.execute(select(MarketNewcomerLog))).scalars().all()
    traj = await compute_trajectories(rows)
    return {r.app_id: traj[r.id] for r in rows}


# 5 个日更快照 06-01..06-05；anchor 每天在榜，锚定 combo「最新快照日」= 06-05。
_DATES = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]


@pytest.mark.asyncio
async def test_climbing(client):
    """检出 #63 → 一路升到 #38（累计 +25 ≥ 阈值）→ climbing，current/peak=38。"""
    await _seed_ranks("anchor", [(d, 1) for d in _DATES])
    await _seed_ranks("climber", [("2026-06-01", 63), ("2026-06-03", 50), ("2026-06-05", 38)])
    await _seed_log("climber", "2026-06-01", 63)
    t = (await _run())["climber"]
    assert t["trend"] == "climbing"
    assert t["current_rank"] == 38
    assert t["peak_rank"] == 38
    assert t["on_chart"] is True
    assert t["current_as_of"] == "2026-06-05"
    assert t["days_tracked"] == 4


@pytest.mark.asyncio
async def test_falling(client):
    """检出 #20 → 跌到 #45（-25 ≤ -阈值）→ falling。"""
    await _seed_ranks("anchor", [(d, 1) for d in _DATES])
    await _seed_ranks("faller", [("2026-06-01", 20), ("2026-06-05", 45)])
    await _seed_log("faller", "2026-06-01", 20)
    t = (await _run())["faller"]
    assert t["trend"] == "falling"
    assert t["current_rank"] == 45
    assert t["peak_rank"] == 20


@pytest.mark.asyncio
async def test_stable(client):
    """检出 #40 → #42（位移 2 < 阈值 5）→ stable。"""
    await _seed_ranks("anchor", [(d, 1) for d in _DATES])
    await _seed_ranks("steady", [("2026-06-01", 40), ("2026-06-05", 42)])
    await _seed_log("steady", "2026-06-01", 40)
    t = (await _run())["steady"]
    assert t["trend"] == "stable"
    assert t["current_rank"] == 42


@pytest.mark.asyncio
async def test_dropped(client):
    """检出后掉出采集深度（最新快照 06-05 里已无它）→ dropped、current_rank=None。"""
    await _seed_ranks("anchor", [(d, 1) for d in _DATES])
    await _seed_ranks("gone", [("2026-06-01", 30), ("2026-06-03", 33)])  # 06-04/05 消失
    await _seed_log("gone", "2026-06-01", 30)
    t = (await _run())["gone"]
    assert t["trend"] == "dropped"
    assert t["on_chart"] is False
    assert t["current_rank"] is None
    assert t["peak_rank"] == 30           # 掉榜也保留检出以来最好名次
    assert t["last_seen"] == "2026-06-03"


@pytest.mark.asyncio
async def test_new_no_followup(client):
    """检出当天即最新快照（无后续数据）→ new、days_tracked=0。"""
    await _seed_ranks("anchor", [(d, 1) for d in _DATES])
    await _seed_ranks("fresh", [("2026-06-05", 40)])
    await _seed_log("fresh", "2026-06-05", 40)
    t = (await _run())["fresh"]
    assert t["trend"] == "new"
    assert t["days_tracked"] == 0
    assert t["current_rank"] == 40
    assert t["on_chart"] is True


@pytest.mark.asyncio
async def test_unknown_no_ranking_points(client):
    """检出行在 game_rankings 里无任何点（历史脏行）→ unknown、字段全空。"""
    await _seed_ranks("anchor", [(d, 1) for d in _DATES])
    await _seed_log("ghost", "2026-06-01", 30)  # 故意不塞 game_rankings
    t = (await _run())["ghost"]
    assert t["trend"] == "unknown"
    assert t["current_rank"] is None
    assert t["peak_rank"] is None
    assert t["on_chart"] is False


@pytest.mark.asyncio
async def test_free_chart_isolated_from_grossing(client):
    """同 app 在 free 榜的轨迹与 grossing 各自独立（chart_type 进 key，不串）。"""
    await _seed_ranks("anchor", [(d, 1) for d in _DATES], chart_type="free")
    await _seed_ranks("dl", [("2026-06-01", 50), ("2026-06-05", 30)], chart_type="free")
    await _seed_log("dl", "2026-06-01", 50, chart_type="free")
    t = (await _run())["dl"]
    assert t["trend"] == "climbing"
    assert t["current_rank"] == 30
