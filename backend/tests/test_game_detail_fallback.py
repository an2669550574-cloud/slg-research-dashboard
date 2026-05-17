"""GET /api/games/{app_id}：非追踪但在排行榜里的真实竞品应合成返回，不 404。

回归点：真实数据接入后，排行榜里 200+ 真实游戏都不在 games 表（只有 seed +
手动添加），点详情页 gamesApi.get 会 404 弹窗、头部空白。conftest 重载
app.* —— import 放函数内。
"""
import pytest


async def _seed_ranking(app_id, name="Whiteout Survival", publisher="Century Games Pte. Ltd."):
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking
    async with AsyncSessionLocal() as db:
        for d in ("2026-05-16", "2026-05-17"):  # 两天，确认取最近一天
            db.add(GameRanking(
                app_id=app_id, date=d, rank=2, downloads=10000, revenue=500000.0,
                country="US", platform="ios", name=name, publisher=publisher,
                icon_url="http://x/icon.png",
            ))
        await db.commit()


@pytest.mark.asyncio
async def test_untracked_ranked_game_synthesized_not_404(client):
    await _seed_ranking("6477682303")
    resp = await client.get("/api/games/6477682303")
    assert resp.status_code == 200
    body = resp.json()
    assert body["app_id"] == "6477682303"
    assert body["name"] == "Whiteout Survival"
    assert body["publisher"] == "Century Games Pte. Ltd."
    assert body["icon_url"] == "http://x/icon.png"
    assert body["id"] == 0  # 合成记录标记


@pytest.mark.asyncio
async def test_tracked_game_still_returned_normally(client):
    from app.database import AsyncSessionLocal
    from app.models.game import Game
    async with AsyncSessionLocal() as db:
        db.add(Game(app_id="tracked.1", name="Tracked", publisher="Acme",
                    platform="ios", country="US"))
        await db.commit()
    resp = await client.get("/api/games/tracked.1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Tracked"
    assert body["id"] != 0  # 真实持久化记录


@pytest.mark.asyncio
async def test_truly_unknown_app_id_still_404(client):
    resp = await client.get("/api/games/does.not.exist.anywhere")
    assert resp.status_code == 404
