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
async def test_products_endpoint_includes_radar(client):
    """/products 端点也并入雷达未上榜产品——与卡片同口径，避免卡片有数抽屉为空。"""
    r = await client.post("/api/publishers/", json={"name": "雷达抽屉主体"})
    eid = r.json()["id"]
    a = await client.post(f"/api/publishers/{eid}/itunes-artists",
                          json={"artist_id": "9002", "platform": "gp", "label": "账号"})
    from app.database import AsyncSessionLocal
    from app.models.publisher import PublisherItunesApp
    async with AsyncSessionLocal() as db:
        db.add(PublisherItunesApp(entity_id=eid, artist_row_id=a.json()["id"],
                                  track_id="com.z.newgame.gp", name="深空奇兵",
                                  artwork_url="https://icon/z.png", is_baseline=True))
        await db.commit()
    products = (await client.get(f"/api/publishers/{eid}/products")).json()
    assert [p["name"] for p in products] == ["深空奇兵"]
    assert products[0]["matched_by"] == "radar"


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
async def test_gaps_excludes_attributed_and_groups_by_publisher(client):
    """缺口端点：alias 命中 / app_id 钉 / 收入为 0 都不算缺口；同 publisher 多 app 合算。
    CJK 数据走一遍——publisher 字段中文不能被规范化吃掉。"""
    today = _today()
    await _seed_rankings([
        # 已 alias 命中（kabam）→ 不算缺口
        ("hit.1", today, 1, 100, 80.0, "US", "ios", "已归属", "Kabam Games Ltd"),
        # 已 app_id 钉 → 不算缺口
        ("pin.1", today, 2, 50, 40.0, "US", "ios", "钉住的", "Some Studio"),
        # 漏网厂 A：两个 app 都归 "Mystery Studio"，应合算
        ("gap.a1", today, 3, 200, 60.0, "US", "ios", "漏网游戏一", "Mystery Studio"),
        ("gap.a2", today, 4, 100, 30.0, "JP", "android", "漏网游戏二", "Mystery Studio"),
        # 漏网厂 B：中文 publisher
        ("gap.b1", today, 5, 150, 45.0, "CN", "ios", "国产漏网", "未知中文厂商"),
        # 收入 0 不计入
        ("zero.1", today, 6, 999, 0.0, "US", "ios", "无收入", "Free Studio"),
    ])
    # 建一个挂 kabam alias 和 pin.1 钉住的主体
    await client.post("/api/publishers/", json={
        "name": "已存主体",
        "aliases": [{"keyword": "kabam"}],
        "app_ids": [{"app_id": "pin.1"}],
    })
    gaps = (await client.get("/api/publishers/gaps")).json()
    pubs = {g["publisher"]: g for g in gaps}
    assert "Kabam Games Ltd" not in pubs  # alias 命中
    assert "Some Studio" not in pubs       # app_id 钉
    assert "Free Studio" not in pubs       # 收入 0
    assert "Mystery Studio" in pubs
    assert pubs["Mystery Studio"]["app_count"] == 2
    assert pubs["Mystery Studio"]["revenue"] == 90.0  # 60 + 30
    assert pubs["Mystery Studio"]["downloads"] == 300  # 200 + 100
    assert pubs["Mystery Studio"]["top_app"]["app_id"] == "gap.a1"  # 收入高的代表
    assert "未知中文厂商" in pubs
    assert pubs["未知中文厂商"]["app_count"] == 1
    # 按收入降序
    revs = [g["revenue"] for g in gaps]
    assert revs == sorted(revs, reverse=True)


@pytest.mark.asyncio
async def test_gaps_respects_window_and_limit(client):
    """days 窗外的不算；limit 截断。"""
    from datetime import date, timedelta as td
    today_d = date.fromisoformat(_today())
    long_ago = (today_d - td(days=60)).isoformat()
    today = today_d.isoformat()
    await _seed_rankings([
        ("old.1", long_ago, 1, 100, 999.0, "US", "ios", "古早", "Ancient Studio"),
        ("new.1", today, 1, 100, 10.0, "US", "ios", "近期", "Fresh Studio"),
    ])
    gaps = (await client.get("/api/publishers/gaps", params={"days": 30})).json()
    pubs = {g["publisher"] for g in gaps}
    assert "Fresh Studio" in pubs
    assert "Ancient Studio" not in pubs  # 60 天前不在 30 天窗口

    # limit=1：截断
    one = (await client.get("/api/publishers/gaps", params={"limit": 1})).json()
    assert len(one) <= 1


@pytest.mark.asyncio
async def test_health_endpoint_covers_audit_dimensions(client):
    """/health 端点：覆盖 tier 分布 + 待补/命名/复核 backlog + 总量。空库 + 加几个主体验各维度。"""
    # 空库基线
    h = (await client.get("/api/publishers/health")).json()
    assert h["total"] == 0
    assert h["tier_primary"] == 0 and h["tier_none"] == 0

    # 主体 A：完整（有 brief + 一手源 + 关系 + 中文名）
    eid_a = (await client.post("/api/publishers/", json={
        "name": "灵犀互娱 Lingxi", "hq_region": "国内",
        "brief": "国内 SLG 大厂；阿里游戏旗下；三国志战略版研发。",
        "aliases": [{"keyword": "lingxi"}],
    })).json()["id"]
    await client.post(f"/api/publishers/{eid_a}/sources", json={
        "url": "https://lingxigames.com", "title": "官网",
        "source_type": "official_domain", "as_of": "2026-06-20",
    })
    # 主体 B：浅度（仅二手源、英文名国内厂、无关系）
    eid_b = (await client.post("/api/publishers/", json={
        "name": "ShallowCo", "hq_region": "国内", "brief": "测试浅度国内主体待补中文名",
    })).json()["id"]
    await client.post(f"/api/publishers/{eid_b}/sources", json={
        "url": "https://example.com/article", "title": "媒体报道",
        "source_type": "media", "as_of": "2024-01-01",  # > 12 个月 → stale
    })
    # 主体 C：纯壳（无 brief、无源、无 alias/appid）
    await client.post("/api/publishers/", json={"name": "EmptyShell"})
    # 关系：A ←→ A 自身不行，再建一个挂关系
    eid_d = (await client.post("/api/publishers/", json={
        "name": "Sub", "is_slg": True, "brief": "子公司，挂母体灵犀作示例关系"
    })).json()["id"]
    await client.post(f"/api/publishers/{eid_a}/relations", json={
        "counterpart_id": eid_d, "counterpart_role": "child", "relation_type": "controlling",
    })

    h = (await client.get("/api/publishers/health")).json()
    assert h["total"] == 4
    assert h["tier_primary"] == 1            # A 一手
    assert h["tier_secondary"] == 1          # B 二手
    assert h["tier_none"] == 2               # C/D 无源
    assert h["no_sources"] == 2              # C/D
    assert h["no_primary_source"] == 1       # B
    assert h["empty_brief"] == 1             # C (无 brief，<30 字)
    assert h["no_aliases_no_appids"] == 3    # B/C/D
    assert h["cn_no_chinese_name"] == 1      # B (国内 + 全英文)
    assert h["stale_review"] == 1            # B (2024-01-01 > 12 个月)
    assert h["total_aliases"] == 1
    assert h["total_sources"] == 2
    assert h["total_relations"] == 1
    assert h["no_relations"] == 2            # B/C 没关系（A/D 都挂了）
    assert h["capital_entities"] == 0        # 都是 is_slg=True 默认
    assert h["max_brief_len"] > 0


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
