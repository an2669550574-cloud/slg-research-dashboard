"""GET /api/games/aggregate-leaderboard：仪表盘「合计·区间」视图数据源。
跨该 app 全部已监测市场在窗口内合计 downloads/revenue，与详情页头部
「已监测市场合计」同口径——数字可直接对账。slg_only 默认开。
"""
import pytest
from datetime import date, timedelta


async def _seed(rows):
    """rows: (app_id, date, rank, downloads, revenue, country, platform, name, publisher)。"""
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking
    async with AsyncSessionLocal() as db:
        for aid, d, rk, dl, rv, c, p, name, pub in rows:
            db.add(GameRanking(app_id=aid, date=d, rank=rk, downloads=dl,
                               revenue=rv, country=c, platform=p,
                               name=name, publisher=pub))
        await db.commit()


@pytest.mark.asyncio
async def test_aggregate_leaderboard_sums_across_markets_filters_slg_and_rank_only(client):
    today = date.today().strftime("%Y-%m-%d")
    earlier = (date.today() - timedelta(days=2)).strftime("%Y-%m-%d")
    await _seed([
        # SLG app（Lilith publisher 命中关键词）跨 2 组合 ×2 天：4 行销量
        ("agg.slg", today,   3, 100, 10.0, "US", "ios", "RoK", "Lilith Games"),
        ("agg.slg", earlier, 3, 200, 20.0, "US", "ios", "RoK", "Lilith Games"),
        ("agg.slg", today,   5,  50,  5.0, "JP", "ios", "RoK", "Lilith Games"),
        ("agg.slg", earlier, 5,  30,  3.0, "JP", "ios", "RoK", "Lilith Games"),
        # 同 app 一个 rank-only 行（下载/收入皆 NULL）—— 不应进合计榜
        ("agg.slg", today,   7, None, None, "KR", "ios", "RoK", "Lilith Games"),
        # 非 SLG（slg_only=true 默认 → 必须被滤掉）
        ("agg.nonslg", today, 1, 9999, 9999.0, "US", "ios", "Tetris", "Random Studio"),
    ])
    r = await client.get("/api/games/aggregate-leaderboard", params={"days": 30})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["app_id"] == "agg.slg"
    # 跨市场 + 跨日累加；rank-only 行未污染汇总
    assert row["downloads"] == 100 + 200 + 50 + 30
    assert row["revenue"] == 10.0 + 20.0 + 5.0 + 3.0
    assert row["publisher"] == "Lilith Games"


@pytest.mark.asyncio
async def test_aggregate_leaderboard_slg_only_false_includes_nonslg_sorted_by_revenue(client):
    today = date.today().strftime("%Y-%m-%d")
    await _seed([
        ("slg.1", today, 1, 10,   1.0,   "US", "ios", "RoK",    "Lilith Games"),
        ("non.1", today, 1, 100,  100.0, "US", "ios", "Tetris", "Random Studio"),
    ])
    r = await client.get("/api/games/aggregate-leaderboard",
                         params={"days": 30, "slg_only": "false"})
    assert r.status_code == 200
    rows = r.json()
    ids = [row["app_id"] for row in rows]
    assert ids == ["non.1", "slg.1"]  # 按 revenue desc


@pytest.mark.asyncio
async def test_aggregate_leaderboard_empty_when_no_local_data(client):
    r = await client.get("/api/games/aggregate-leaderboard", params={"days": 30})
    assert r.status_code == 200
    assert r.json() == []
