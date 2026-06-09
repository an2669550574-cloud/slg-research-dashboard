"""厂商主体 CRUD + 旗下产品聚合 + is_slg 索引随 CRUD 即时刷新。

conftest 每个 test 重载 app.*、noop seed_publishers（表起步为空）—— import 放函数内。
"""
import pytest
from datetime import date


async def _seed_rankings(rows):
    """rows: (app_id, date, rank, downloads, revenue, country, platform, name, publisher)。"""
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking
    async with AsyncSessionLocal() as db:
        for aid, d, rk, dl, rv, c, p, name, pub in rows:
            db.add(GameRanking(app_id=aid, date=d, rank=rk, downloads=dl, revenue=rv,
                               country=c, platform=p, name=name, publisher=pub))
        await db.commit()


@pytest.mark.asyncio
async def test_list_empty_when_no_seed(client):
    r = await client.get("/api/publishers/")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_create_publisher_with_children_and_readback(client):
    payload = {
        "name": "江娱互动", "name_en": "Jiangyu", "hq_region": "国内",
        "is_slg": True, "brief": "国内 SLG 厂商",
        "aliases": [{"keyword": "jiangyu", "label": "Jiangyu"}],
        "app_ids": [{"app_id": "com.jy.lastwar", "note": "示例单品"}],
    }
    r = await client.post("/api/publishers/", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "江娱互动"
    assert body["hq_region"] == "国内"
    assert [a["keyword"] for a in body["aliases"]] == ["jiangyu"]
    assert [a["app_id"] for a in body["app_ids"]] == ["com.jy.lastwar"]

    # 列表回读含该主体
    lst = (await client.get("/api/publishers/")).json()
    assert any(e["name"] == "江娱互动" for e in lst)


@pytest.mark.asyncio
async def test_products_aggregate_alias_and_appid_match(client):
    today = date.today().strftime("%Y-%m-%d")
    await _seed_rankings([
        ("p.alias", today, 1, 100, 50.0, "US", "ios", "末日要塞", "Kabam Games Ltd"),
        ("com.foo.pin", today, 2, 80, 40.0, "US", "ios", "钉住的竞品", "Unknown Studio"),
        ("p.other", today, 3, 70, 30.0, "US", "ios", "无关游戏", "Other Studio"),
    ])
    payload = {
        "name": "测试主体", "is_slg": True,
        "aliases": [{"keyword": "kabam", "label": "Kabam"}],
        "app_ids": [{"app_id": "com.foo.pin", "note": "精确钉"}],
    }
    eid = (await client.post("/api/publishers/", json=payload)).json()["id"]

    prods = (await client.get(f"/api/publishers/{eid}/products")).json()
    by_id = {p["app_id"]: p for p in prods}
    assert set(by_id) == {"p.alias", "com.foo.pin"}  # p.other 不归属本主体
    assert by_id["p.alias"]["matched_by"] == "alias"
    assert by_id["com.foo.pin"]["matched_by"] == "app_id"
    assert by_id["p.alias"]["revenue"] == 50.0


@pytest.mark.asyncio
async def test_product_count_in_list(client):
    today = date.today().strftime("%Y-%m-%d")
    await _seed_rankings([
        ("c.1", today, 1, 10, 5.0, "US", "ios", "游戏一", "Kabam Games"),
        ("c.2", today, 2, 10, 5.0, "US", "ios", "游戏二", "Kabam Inc"),
    ])
    await client.post("/api/publishers/", json={
        "name": "数数主体", "aliases": [{"keyword": "kabam"}], "app_ids": [],
    })
    lst = (await client.get("/api/publishers/")).json()
    e = next(x for x in lst if x["name"] == "数数主体")
    assert e["product_count"] == 2


@pytest.mark.asyncio
async def test_add_alias_refreshes_is_slg_index(client):
    """新增 alias 后 is_slg 内存索引即时刷新——经 aggregate-leaderboard（走 is_slg）验证。"""
    today = date.today().strftime("%Y-%m-%d")
    await _seed_rankings([
        ("ref.1", today, 1, 200, 99.0, "US", "ios", "刷新验证", "ZenithPlay Ltd"),
    ])
    # 初始：ZenithPlay 不在白名单 → slg_only 默认过滤掉
    before = (await client.get("/api/games/aggregate-leaderboard", params={"days": 30})).json()
    assert all(row["app_id"] != "ref.1" for row in before)

    eid = (await client.post("/api/publishers/", json={"name": "Zenith"})).json()["id"]
    r = await client.post(f"/api/publishers/{eid}/aliases", json={"keyword": "zenithplay"})
    assert r.status_code == 201

    # 刷新后：is_slg 命中 → 进合计榜
    after = (await client.get("/api/games/aggregate-leaderboard", params={"days": 30})).json()
    assert any(row["app_id"] == "ref.1" for row in after)


@pytest.mark.asyncio
async def test_delete_alias_refreshes_index(client):
    today = date.today().strftime("%Y-%m-%d")
    await _seed_rankings([
        ("del.1", today, 1, 200, 99.0, "US", "ios", "删除验证", "ZenithPlay Ltd"),
        ("keep.1", today, 2, 150, 88.0, "US", "ios", "保留命中", "Kabam Games"),
    ])
    eid = (await client.post("/api/publishers/", json={
        "name": "Zenith", "aliases": [{"keyword": "zenithplay"}],
    })).json()["id"]
    # 另留一个无关主体，使删除后 alias 表仍非空（贴近生产：种子主体常驻，不触发空库兜底）
    await client.post("/api/publishers/", json={"name": "KeepCo", "aliases": [{"keyword": "kabam"}]})
    alias_id = (await client.get(f"/api/publishers/{eid}")).json()["aliases"][0]["id"]
    # 命中态
    hit = (await client.get("/api/games/aggregate-leaderboard", params={"days": 30})).json()
    assert any(row["app_id"] == "del.1" for row in hit)
    # 删 zenithplay alias → 索引刷新 → del.1 失效，keep.1（kabam）仍命中
    await client.delete(f"/api/publishers/{eid}/aliases/{alias_id}")
    after = (await client.get("/api/games/aggregate-leaderboard", params={"days": 30})).json()
    ids = {row["app_id"] for row in after}
    assert "del.1" not in ids
    assert "keep.1" in ids


@pytest.mark.asyncio
async def test_delete_publisher_cascades_children(client):
    eid = (await client.post("/api/publishers/", json={
        "name": "待删", "aliases": [{"keyword": "foo"}], "app_ids": [{"app_id": "com.x.y"}],
    })).json()["id"]
    assert (await client.delete(f"/api/publishers/{eid}")).status_code == 200
    assert (await client.get(f"/api/publishers/{eid}")).status_code == 404
    # 子行已级联删除：往已删主体加 alias 应 404
    assert (await client.post(f"/api/publishers/{eid}/aliases", json={"keyword": "z"})).status_code == 404


@pytest.mark.asyncio
async def test_get_404(client):
    assert (await client.get("/api/publishers/99999")).status_code == 404


@pytest.mark.asyncio
async def test_seed_publishers_idempotent(client):
    """直接调 scheduler.seed_publishers_if_empty 两次：只灌一次，主体数 = 种子全集。"""
    import importlib
    scheduler = importlib.import_module("app.scheduler")
    slg = importlib.import_module("app.services.slg_publishers")
    from app.database import AsyncSessionLocal
    from app.models.publisher import PublisherEntity
    from sqlalchemy import select, func

    await scheduler.seed_publishers_if_empty()
    await scheduler.seed_publishers_if_empty()  # 第二次应跳过
    async with AsyncSessionLocal() as db:
        n = (await db.execute(select(func.count()).select_from(PublisherEntity))).scalar_one()
    assert n == len(slg.SEED_PUBLISHERS)
