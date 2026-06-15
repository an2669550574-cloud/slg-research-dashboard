"""看板维护订阅公众号：CRUD + searchbiz 代理 + 服务读 DB。"""
import pytest

from app.services import wechat_articles


@pytest.mark.asyncio
async def test_account_crud_lifecycle(client):
    # 初始空
    r = await client.get("/api/wechat-accounts/")
    assert r.status_code == 200 and r.json() == []

    # 创建
    r = await client.post("/api/wechat-accounts/", json={"fakeid": "FID_A==", "name": "游戏葡萄"})
    assert r.status_code == 201
    acc = r.json()
    assert acc["name"] == "游戏葡萄" and acc["enabled"] is True
    aid = acc["id"]

    # 重复 fakeid → 409
    r = await client.post("/api/wechat-accounts/", json={"fakeid": "FID_A==", "name": "重复"})
    assert r.status_code == 409

    # 列表有 1 条
    assert len(((await client.get("/api/wechat-accounts/")).json())) == 1

    # 停用
    r = await client.patch(f"/api/wechat-accounts/{aid}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False

    # 删除
    assert (await client.delete(f"/api/wechat-accounts/{aid}")).status_code == 200
    assert (await client.get("/api/wechat-accounts/")).json() == []


@pytest.mark.asyncio
async def test_search_requires_enabled(client):
    # 默认 WECHAT_ENABLED=False → 搜索 409
    r = await client.get("/api/wechat-accounts/search", params={"query": "游戏葡萄"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_search_returns_candidates(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "WECHAT_ENABLED", True)

    async def fake_search_biz(query, limit=8):
        return [{"fakeid": "FID_X==", "nickname": "手游那点事", "alias": "shouyou"}]
    # 路由 import 的是 services.wechat_articles.search_biz 的引用，patch 模块属性即可
    monkeypatch.setattr("app.routers.wechat.search_biz", fake_search_biz)

    r = await client.get("/api/wechat-accounts/search", params={"query": "手游"})
    assert r.status_code == 200
    body = r.json()
    assert body[0]["fakeid"] == "FID_X==" and body[0]["nickname"] == "手游那点事"


@pytest.mark.asyncio
async def test_enabled_accounts_reads_db_then_falls_back(client):
    """search 路径的 _enabled_accounts：DB 有启用号读 DB；全停用/空则回退种子。"""
    # 空表 → 回退种子
    assert await wechat_articles._enabled_accounts() == dict(wechat_articles._SEED_ACCOUNTS)

    # 加一个启用号 → 读 DB
    await client.post("/api/wechat-accounts/", json={"fakeid": "FID_B==", "name": "竞核"})
    accs = await wechat_articles._enabled_accounts()
    assert accs == {"竞核": "FID_B=="}
