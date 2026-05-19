"""GET /api/games/{app_id}/coverage：列出该 app 本地实际有数据的
(国家,平台) 组合，按"销量覆盖最全→最少"排，items[0] 即详情页最佳默认。
修复"详情页死写 US/ios 导致只在安卓/JP/KR 进榜的游戏三图全空"。
"""
import pytest


async def _seed(rows):
    """rows: (app_id, date, rank, downloads, revenue, country, platform)。"""
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking
    async with AsyncSessionLocal() as db:
        for aid, d, rk, dl, rv, c, p in rows:
            db.add(GameRanking(app_id=aid, date=d, rank=rk, downloads=dl,
                               revenue=rv, country=c, platform=p))
        await db.commit()


@pytest.mark.asyncio
async def test_coverage_orders_by_sales_density(client):
    await _seed([
        # US/android：3 个销量天 + 1 个 rank=NULL 回填行（无销量）
        ("multi.1", "2026-05-14", None, None, None, "US", "android"),
        ("multi.1", "2026-05-15", 5, 100, 10.0, "US", "android"),
        ("multi.1", "2026-05-16", 4, 200, 20.0, "US", "android"),
        ("multi.1", "2026-05-17", 3, 300, 30.0, "US", "android"),
        # JP/ios：2 个销量天
        ("multi.1", "2026-05-16", 8, 50, 5.0, "JP", "ios"),
        ("multi.1", "2026-05-17", 7, 60, 6.0, "JP", "ios"),
        # US/ios：1 个销量天
        ("multi.1", "2026-05-17", 9, 10, 1.0, "US", "ios"),
        # 另一 app，不应混入
        ("other.x", "2026-05-17", 1, 1, 1.0, "US", "ios"),
    ])
    r = await client.get("/api/games/multi.1/coverage")
    assert r.status_code == 200
    cov = r.json()
    # 销量天数降序：US/android(3) → JP/ios(2) → US/ios(1)
    assert [(c["country"], c["platform"]) for c in cov] == [
        ("US", "android"), ("JP", "ios"), ("US", "ios"),
    ]
    andro = cov[0]
    assert andro["days"] == 4          # 含 rank=NULL 回填行
    assert andro["sales_days"] == 3    # 决定收入/下载图能否画出
    assert andro["rank_days"] == 3     # rank=NULL 行不计


@pytest.mark.asyncio
async def test_coverage_empty_when_no_local_rows(client):
    r = await client.get("/api/games/nobody.zzz/coverage")
    assert r.status_code == 200
    assert r.json() == []
