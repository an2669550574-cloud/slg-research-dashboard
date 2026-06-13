"""厂商主体 CRUD + 旗下产品聚合 + is_slg 索引随 CRUD 即时刷新。

conftest 每个 test 重载 app.*、noop seed_publishers（表起步为空）—— import 放函数内。
"""
import pytest


def _today() -> str:
    """与 /products 端点窗口右界（utcnow_naive().date()）同源的 UTC 当天。

    端点用 UTC 算窗口、若种子用本地 date.today()，北京凌晨（UTC 仍是前一天）
    种子会落在窗口右界之外 → 查不到任何行。统一走 UTC 消除这个跨午夜偏差。
    """
    from app.database import utcnow_naive
    return utcnow_naive().date().strftime("%Y-%m-%d")


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
    today = _today()
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
    today = _today()
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
async def test_top_products_in_list(client):
    """折叠态图标锚点：旗下产品按收入降序取前 3，带 icon_url。"""
    today = _today()
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking
    async with AsyncSessionLocal() as db:
        for aid, rev, name in [("t.1", 30.0, "高收入"), ("t.2", 10.0, "中收入"),
                               ("t.3", 50.0, "最高收入"), ("t.4", 1.0, "低收入")]:
            db.add(GameRanking(app_id=aid, date=today, rank=1, downloads=10, revenue=rev,
                               country="US", platform="ios", name=name, publisher="Tako Games",
                               icon_url=f"https://icon/{aid}.png"))
        await db.commit()
    await client.post("/api/publishers/", json={
        "name": "图标主体", "aliases": [{"keyword": "tako"}], "app_ids": [],
    })
    e = next(x for x in (await client.get("/api/publishers/")).json() if x["name"] == "图标主体")
    assert e["product_count"] == 4
    # 收入降序的前 3：最高(50)/高(30)/中(10)；低(1)被截掉
    assert [p["name"] for p in e["top_products"]] == ["最高收入", "高收入", "中收入"]
    assert e["top_products"][0]["icon_url"] == "https://icon/t.3.png"


@pytest.mark.asyncio
async def test_products_include_radar_unranked(client):
    """雷达 itunes_apps 里未上榜的软启动新品也算旗下产品（治新厂商 product_count=0）。
    同名跨平台（iOS+GP 同款）去重只计一款。"""
    r = await client.post("/api/publishers/", json={"name": "雷达专属主体"})
    eid = r.json()["id"]
    a = await client.post(f"/api/publishers/{eid}/itunes-artists",
                          json={"artist_id": "9001", "platform": "ios", "label": "雷达账号"})
    artist_row_id = a.json()["id"]

    from app.database import AsyncSessionLocal
    from app.models.publisher import PublisherItunesApp
    async with AsyncSessionLocal() as db:
        for track_id, name, art in [
            ("9100", "星海远征", "https://icon/sea.png"),       # iOS（未上任何榜）
            ("com.x.star.gp", "星海远征", "https://icon/sea2.png"),  # GP 同款 → 去重
            ("com.y.deep.gp", "深空要塞", "https://icon/deep.png"),
        ]:
            db.add(PublisherItunesApp(entity_id=eid, artist_row_id=artist_row_id,
                                      track_id=track_id, name=name, artwork_url=art,
                                      is_baseline=True))
        await db.commit()

    e = next(x for x in (await client.get("/api/publishers/")).json() if x["name"] == "雷达专属主体")
    assert e["product_count"] == 2  # 星海远征（去重）+ 深空要塞
    names = {p["name"] for p in e["top_products"]}
    assert names == {"星海远征", "深空要塞"}
    icons = {p["icon_url"] for p in e["top_products"]}
    assert "https://icon/sea.png" in icons  # 同名取先出现的 iOS 行图标


@pytest.mark.asyncio
async def test_add_alias_refreshes_is_slg_index(client):
    """新增 alias 后 is_slg 内存索引即时刷新——经 aggregate-leaderboard（走 is_slg）验证。"""
    today = _today()
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
    today = _today()
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
