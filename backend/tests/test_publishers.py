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
async def test_gaps_us_first_for_cjk_multimarket_collision(client):
    """同一 app_id 跨市场同时有 US-Latin 行 + JP/KR-CJK 行时，/gaps 必须 US 优先取代表
    publisher/name——否则裸 MAX 按 Unicode 排序偏向 CJK → publisher 被 _toks 切成空 token →
    ① alias 漏匹（已建档厂错误地重新冒成缺口）② 缺口卡显示日韩本地化名。
    回归「CJK MAX 偏向」修复在 /gaps 出口的覆盖（此前只修了 _ranking_pairs / products 两处）。"""
    today = _today()
    await _seed_rankings([
        # 已建档厂：同 app_id，US 行 Latin publisher，JP 行本地化 CJK publisher
        ("attr.1", today, 1, 100, 80.0, "US", "ios", "Frost Forge", "FrostForge Ltd"),
        ("attr.1", today, 1, 90, 70.0, "JP", "ios", "フロストフォージ", "フロストフォージ"),
        # 真漏网厂：同 app_id，US 行 Latin，KR 行本地化 CJK → 应进缺口，但代表名取 Latin
        ("gap.1", today, 2, 200, 60.0, "US", "ios", "Last Citadel", "Citadel Mobile Inc"),
        ("gap.1", today, 2, 180, 50.0, "KR", "android", "라스트 시타델", "시타델 모바일"),
    ])
    # 建主体挂 alias，按 US-Latin publisher 命中（裸 MAX 取 CJK 时该 alias 会漏匹）
    await client.post("/api/publishers/", json={
        "name": "霜炉", "aliases": [{"keyword": "frostforge"}],
    })
    gaps = (await client.get("/api/publishers/gaps")).json()
    pubs = {g["publisher"]: g for g in gaps}
    # 已建档厂被 alias 命中 → 不在缺口（US 优先取到 Latin 串才能 token 化匹配）
    assert "FrostForge Ltd" not in pubs
    assert "フロストフォージ" not in pubs
    # 真漏网厂进缺口，且 publisher 与 top_app.name 取 US-Latin 而非 CJK
    assert "Citadel Mobile Inc" in pubs
    assert "시타델 모바일" not in pubs
    assert pubs["Citadel Mobile Inc"]["app_count"] == 1
    assert pubs["Citadel Mobile Inc"]["top_app"]["name"] == "Last Citadel"


@pytest.mark.asyncio
async def test_gaps_confidence_signals_days_and_newcomer_join(client):
    """缺口置信信号：days_on_chart=旗舰窗口内上榜的不同天数（持续上榜 vs 一日闪现）；
    genre/summary_cn 由 gaps→newcomer_log 回流（同 app_id join 出玩法品类+一句话）。"""
    from datetime import date, timedelta as td
    from app.database import AsyncSessionLocal
    from app.models.newcomer import MarketNewcomerLog
    d0 = date.fromisoformat(_today())
    days3 = [(d0 - td(days=i)).isoformat() for i in range(3)]
    rows = [("gapconf.1", dt, 5, 100, 60.0, "US", "ios", "Conf Game", "ConfCorp Studios")
            for dt in days3]  # 同 app 连上 3 天
    rows.append(("gapconf.2", days3[0], 8, 50, 20.0, "US", "ios", "Blip Game", "BlipCo"))  # 仅 1 天
    await _seed_rankings(rows)
    async with AsyncSessionLocal() as db:  # 仅给 gapconf.1 配 newcomer_log（回流来源）
        db.add(MarketNewcomerLog(
            country="US", platform="ios", app_id="gapconf.1", as_of=days3[0],
            chart_type="grossing", is_slg=False, name="Conf Game",
            publisher="ConfCorp Studios", genre="Strategy", summary_cn="末日策略测试摘要"))
        await db.commit()

    gaps = (await client.get("/api/publishers/gaps?days=30")).json()
    by_pub = {g["publisher"]: g for g in gaps}
    assert by_pub["ConfCorp Studios"]["days_on_chart"] == 3
    assert by_pub["ConfCorp Studios"]["genre"] == "Strategy"
    assert by_pub["ConfCorp Studios"]["summary_cn"] == "末日策略测试摘要"
    assert by_pub["BlipCo"]["days_on_chart"] == 1     # 一日闪现
    assert by_pub["BlipCo"]["genre"] is None          # 无 newcomer_log 回流 → None


@pytest.mark.asyncio
async def test_add_alias_and_app_id_reject_duplicates(client):
    """同主体下重复 alias / app_id 返回 409（对齐 itunes-artist / relation 的去重约定），
    避免人工建档时抽屉出现重复马甲行。跨主体钉同一 app_id 仍允许（另一层语义）。"""
    eid = (await client.post("/api/publishers/", json={"name": "去重测试"})).json()["id"]
    # alias 幂等：strip 后同名也判重
    assert (await client.post(f"/api/publishers/{eid}/aliases", json={"keyword": "funplus"})).status_code == 201
    assert (await client.post(f"/api/publishers/{eid}/aliases", json={"keyword": "funplus"})).status_code == 409
    assert (await client.post(f"/api/publishers/{eid}/aliases", json={"keyword": " funplus "})).status_code == 409
    # app_id 幂等
    assert (await client.post(f"/api/publishers/{eid}/app-ids", json={"app_id": "com.fp.game"})).status_code == 201
    assert (await client.post(f"/api/publishers/{eid}/app-ids", json={"app_id": "com.fp.game"})).status_code == 409
    # 不同主体钉同一 app_id 仍允许（按 entity 维度去重，非全局）
    eid2 = (await client.post("/api/publishers/", json={"name": "另一主体"})).json()["id"]
    assert (await client.post(f"/api/publishers/{eid2}/app-ids", json={"app_id": "com.fp.game"})).status_code == 201
    # 去重生效：只各剩一条
    body = (await client.get(f"/api/publishers/{eid}")).json()
    assert len(body["aliases"]) == 1
    assert len(body["app_ids"]) == 1
    # 内联建主体路径同样去重（payload 自带重复 keyword/app_id 只写一条），与端点口径一致
    inline = (await client.post("/api/publishers/", json={
        "name": "内联去重",
        "aliases": [{"keyword": "kabam"}, {"keyword": "kabam"}, {"keyword": " kabam "}],
        "app_ids": [{"app_id": "com.k.g"}, {"app_id": "com.k.g"}],
    })).json()
    assert len(inline["aliases"]) == 1
    assert len(inline["app_ids"]) == 1


@pytest.mark.asyncio
async def test_health_reports_itunes_artist_coverage(client):
    """health 端点暴露 iOS 雷达覆盖率：total_itunes_artists + entities_without_itunes_artist。
    只统计 platform='ios'（雷达 sync_itunes_releases 只跑 iOS；GP 账号不计）。"""
    e1 = (await client.post("/api/publishers/", json={"name": "接雷达的厂"})).json()["id"]
    (await client.post("/api/publishers/", json={"name": "没接雷达的厂"}))  # 无 artist
    r = await client.post(f"/api/publishers/{e1}/itunes-artists",
                          json={"artist_id": "123456", "platform": "ios"})
    assert r.status_code == 201
    h = (await client.get("/api/publishers/health")).json()
    assert h["total"] == 2
    assert h["total_itunes_artists"] == 1
    assert h["entities_without_itunes_artist"] == 1  # 2 主体 - 1 已接


@pytest.mark.asyncio
async def test_itunes_artist_suggestions_mock_mode_empty(client):
    """mock 模式不出外网 → 雷达覆盖建议恒空（默认 USE_MOCK_DATA=true）。"""
    await client.post("/api/publishers/", json={
        "name": "有 iOS app 的厂", "app_ids": [{"app_id": "111"}],
    })
    assert (await client.get("/api/publishers/itunes-artist-suggestions")).json() == []


@pytest.mark.asyncio
async def test_itunes_artist_suggestions_resolves_and_filters(client, monkeypatch):
    """雷达覆盖建议：未接雷达的 is_slg 主体从已钉 iOS app_id 反解开发者账号；
    已覆盖/资本方/无 iOS app_id/artist 已占用 全部排除。"""
    from app.config import settings
    import app.services.itunes_releases as svc

    # A: is_slg、有 iOS 数字 app_id、未接雷达 → 应出现
    a = (await client.post("/api/publishers/", json={
        "name": "工作室A", "is_slg": True, "app_ids": [{"app_id": "111"}],
    })).json()["id"]
    # B: 已接雷达（artist=800）→ 不出现（covered），且全局占用 800
    b = (await client.post("/api/publishers/", json={
        "name": "已接雷达B", "is_slg": True, "app_ids": [{"app_id": "222"}],
    })).json()["id"]
    assert (await client.post(f"/api/publishers/{b}/itunes-artists",
                              json={"artist_id": "800", "platform": "ios"})).status_code == 201
    # C: 资本方（is_slg=False）→ 不出现（非雷达目标）
    await client.post("/api/publishers/", json={
        "name": "资本方C", "is_slg": False, "app_ids": [{"app_id": "333"}],
    })
    # D: is_slg 但只有 Android 包名（非数字）→ iOS 侧不出现（数字 id 才能 iTunes 反解；
    # Android 走 GP 侧，本测试聚焦 iOS 过滤、把 GP 反解打桩为空，GP 路径另有专测）
    await client.post("/api/publishers/", json={
        "name": "仅安卓D", "is_slg": True, "app_ids": [{"app_id": "com.x.y"}],
    })
    # E: is_slg、iOS app_id 反解出的 artist 已被 B 占用 → 跳过
    await client.post("/api/publishers/", json={
        "name": "撞车E", "is_slg": True, "app_ids": [{"app_id": "444"}],
    })

    # 每个非 A 主体的 app 都映射到一个「未被占用」的可解析 artist——这样若任一排除分支
    # （covered / is_slg / isdigit）被误删，对应主体会**多出一行**，len 立即 ≠ 1（让过滤器
    # 真正 load-bearing，不靠 resolve→None 兜底假绿）。唯独 444→800 撞 B 已占用，测去重分支。
    table = {
        "111": {"artist_id": "900", "artist_name": "Studio A", "app_name": "Game A"},
        "222": {"artist_id": "801", "artist_name": "Studio B", "app_name": "Game B"},      # B covered → 删 covered 过滤才会泄漏
        "333": {"artist_id": "802", "artist_name": "Studio C", "app_name": "Game C"},      # C 资本方 → 删 is_slg 过滤才会泄漏
        "com.x.y": {"artist_id": "803", "artist_name": "Studio D", "app_name": "Game D"},  # D 安卓 → 删 isdigit 过滤才会泄漏
        "444": {"artist_id": "800", "artist_name": "撞车工作室", "app_name": "Game E"},     # E 撞 B 已占用 800 → 测去重分支
    }

    async def fake_resolve(app_id):
        return table.get(app_id)

    async def _no_gp(pkg):  # 聚焦 iOS 过滤：GP 反解打桩为空（D 的 Android 走 GP，专测另有）
        return None

    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(svc, "resolve_artist_for_app", fake_resolve)
    monkeypatch.setattr("app.services.gp_releases.resolve_gp_developer_for_package", _no_gp)
    monkeypatch.setattr("app.routers.publishers._SUGGEST_LOOKUP_DELAY_S", 0)

    sugg = (await client.get("/api/publishers/itunes-artist-suggestions")).json()
    assert {s["entity_id"] for s in sugg} == {a}  # 仅 A；B/C/D/E 全被各自分支排除
    s = sugg[0]
    assert s["entity_name"] == "工作室A"
    assert s["platform"] == "ios"
    assert s["source_app_id"] == "111"
    assert s["artist_id"] == "900"
    assert s["artist_name"] == "Studio A"
    assert s["source_app_name"] == "Game A"


@pytest.mark.asyncio
async def test_itunes_artist_suggestions_from_matched_products(client, monkeypatch):
    """slice 2：未钉任何 app、只靠 alias 归属的 SLG 主体，也能从 alias 匹配到的 iOS 产品
    反解 artistId（大量真 SLG 单厂属此类）。source_app_id 来自匹配产品而非 pinned。"""
    from app.config import settings
    import app.services.itunes_releases as svc
    today = _today()
    # 一个 iOS 产品，publisher 命中下面主体的 alias；该主体不钉任何 app_id（slice 1 覆盖不到）
    await _seed_rankings([
        ("7001", today, 1, 500, 300.0, "US", "ios", "Mega Strategy", "MegaCorp Studios"),
        # 同主体第二个 iOS 产品收入更高 → 应优先用它反解（旗舰先解）
        ("7002", today, 2, 800, 900.0, "US", "ios", "Mega War", "MegaCorp Studios"),
        # Android 包名产品 → iOS 侧不选（走 GP，本测试 GP 反解打桩为空，聚焦 iOS 旗舰优先）
        ("com.mega.x", today, 3, 100, 50.0, "US", "android", "Mega Mobile", "MegaCorp Studios"),
    ])
    eid = (await client.post("/api/publishers/", json={
        "name": "兆业", "is_slg": True, "aliases": [{"keyword": "megacorp"}],  # 无 app_ids
    })).json()["id"]

    table = {
        "7002": {"artist_id": "950", "artist_name": "MegaCorp", "app_name": "Mega War"},
        "7001": {"artist_id": "951", "artist_name": "MegaCorp", "app_name": "Mega Strategy"},
    }

    async def fake_resolve(app_id):
        return table.get(app_id)

    async def _no_gp(pkg):
        return None

    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(svc, "resolve_artist_for_app", fake_resolve)
    monkeypatch.setattr("app.services.gp_releases.resolve_gp_developer_for_package", _no_gp)
    monkeypatch.setattr("app.routers.publishers._SUGGEST_LOOKUP_DELAY_S", 0)

    sugg = (await client.get("/api/publishers/itunes-artist-suggestions")).json()
    assert {s["entity_id"] for s in sugg} == {eid}
    s = next(x for x in sugg if x["platform"] == "ios")
    assert s["source_app_id"] == "7002"  # 旗舰（收入最高的 iOS 匹配产品）先反解
    assert s["artist_id"] == "950"


@pytest.mark.asyncio
async def test_itunes_artist_suggestions_skips_occupied_artist_tries_next(client, monkeypatch):
    """旗舰反解出的开发者账号已被占用时，应试本主体下一候选而非放弃整主体
    （多候选结构下 break→continue 修漏报；单 pinned 时两者等价）。"""
    from app.config import settings
    import app.services.itunes_releases as svc
    today = _today()
    await _seed_rankings([
        ("8001", today, 1, 900, 900.0, "US", "ios", "Twin Flagship", "TwinCo Ltd"),  # 旗舰 → 已占用账号
        ("8002", today, 2, 100, 100.0, "US", "ios", "Twin Minor", "TwinCo Ltd"),      # 次品 → 未占用账号
    ])
    # 占位主体先接入 artist 700，让旗舰 8001 反解出的账号成为「已占用」
    occ = (await client.post("/api/publishers/", json={"name": "占位厂", "is_slg": True})).json()["id"]
    assert (await client.post(f"/api/publishers/{occ}/itunes-artists",
                              json={"artist_id": "700", "platform": "ios"})).status_code == 201
    x = (await client.post("/api/publishers/", json={
        "name": "双子", "is_slg": True, "aliases": [{"keyword": "twinco"}],
    })).json()["id"]

    table = {
        "8001": {"artist_id": "700", "artist_name": "TwinCo A", "app_name": "Twin Flagship"},  # 占用
        "8002": {"artist_id": "701", "artist_name": "TwinCo B", "app_name": "Twin Minor"},     # 自由
    }

    async def fake_resolve(app_id):
        return table.get(app_id)

    async def _no_gp(pkg):
        return None

    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(svc, "resolve_artist_for_app", fake_resolve)
    monkeypatch.setattr("app.services.gp_releases.resolve_gp_developer_for_package", _no_gp)
    monkeypatch.setattr("app.routers.publishers._SUGGEST_LOOKUP_DELAY_S", 0)

    sugg = (await client.get("/api/publishers/itunes-artist-suggestions")).json()
    by_eid = {s["entity_id"]: s for s in sugg}
    assert x in by_eid  # 旗舰账号被占用没导致整主体被跳过
    assert by_eid[x]["source_app_id"] == "8002"  # 退到次品反解出未占用账号
    assert by_eid[x]["artist_id"] == "701"


@pytest.mark.asyncio
async def test_gp_artist_suggestions_from_android(client, monkeypatch):
    """GP 侧雷达覆盖建议：未接 GP 雷达的 is_slg 主体，从安卓包名（pinned/alias 匹配产品）反解
    GP 开发者 id，platform='gp'——治 GP-only SLG 在面板失明。已接 GP 雷达的主体不再建议。"""
    from app.config import settings
    today = _today()
    await _seed_rankings([
        ("com.geeker.gok", today, 5, 200, 400.0, "US", "android", "Game of Kings", "LIGHTNING STUDIOS"),
    ])
    # A: 只钉安卓包名、未接 GP 雷达 → 应出 GP 建议
    a = (await client.post("/api/publishers/", json={
        "name": "雷电工作室", "is_slg": True, "app_ids": [{"app_id": "com.geeker.gok"}],
    })).json()["id"]
    # B: 钉安卓包名但已接 GP 雷达 → 不出现（gp_covered，删该过滤才会泄漏）
    b = (await client.post("/api/publishers/", json={
        "name": "已接GP的B", "is_slg": True, "app_ids": [{"app_id": "com.b.pkg"}],
    })).json()["id"]
    assert (await client.post(f"/api/publishers/{b}/itunes-artists",
                              json={"artist_id": "gpdevB", "platform": "gp"})).status_code == 201

    async def fake_gp(pkg):
        return {"com.geeker.gok": {"artist_id": "8266249258995725273",
                                   "artist_name": "LIGHTNING STUDIOS", "app_name": "Game of Kings"},
                "com.b.pkg": {"artist_id": "gpZ", "artist_name": "B", "app_name": "BGame"}}.get(pkg)

    async def _no_ios(app_id):
        return None

    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr("app.services.gp_releases.resolve_gp_developer_for_package", fake_gp)
    monkeypatch.setattr("app.services.itunes_releases.resolve_artist_for_app", _no_ios)
    monkeypatch.setattr("app.routers.publishers._SUGGEST_LOOKUP_DELAY_S", 0)

    sugg = (await client.get("/api/publishers/itunes-artist-suggestions")).json()
    assert {s["entity_id"] for s in sugg} == {a}  # 仅 A；B 已接 GP 雷达被排除
    s = sugg[0]
    assert s["platform"] == "gp"
    assert s["artist_id"] == "8266249258995725273"
    assert s["source_app_id"] == "com.geeker.gok"
    assert s["artist_name"] == "LIGHTNING STUDIOS"


@pytest.mark.asyncio
async def test_download_leads_filters_dedups_and_excludes(client):
    """下载榜早期信号：free + is_slg=false + genre=Strategy + 非忽略 → 入；grossing /
    is_slg=true / 非 Strategy / reentry / 忽略 → 排除；跨市场同 app 收敛留一条且富化回填。"""
    from app.database import AsyncSessionLocal
    from app.models.newcomer import MarketNewcomerLog

    def mk(**kw):
        base = dict(country="US", platform="android", as_of="2026-06-29",
                    chart_type="free", is_slg=False, is_reentry=False)
        base.update(kw)
        return MarketNewcomerLog(**base)

    async with AsyncSessionLocal() as db:
        db.add_all([
            mk(app_id="lead1", name="新厂A", publisher="NewCo A", genre="Strategy", rank=20),
            # 同 app 第二市场 genre 缺失 → 应被收敛进 lead1 并回填 genre 后仍命中
            mk(app_id="lead1", platform="ios", name="新厂A", publisher="NewCo A", genre=None, rank=5),
            mk(app_id="lead2", name="新厂B", publisher="NewCo B", genre="Strategy Games", rank=30),
            mk(app_id="x_grossing", chart_type="grossing", name="收入榜", publisher="GCo", genre="Strategy"),
            mk(app_id="x_slg", is_slg=True, name="已识别", publisher="SLGCo", genre="Strategy"),
            mk(app_id="x_puzzle", name="消除", publisher="PuzzleCo", genre="Puzzle"),
            mk(app_id="x_reentry", is_reentry=True, name="回归", publisher="OldCo", genre="Strategy"),
            mk(app_id="x_ig_app", name="忽略app", publisher="IgAppCo", genre="Strategy"),
            mk(app_id="x_ig_pub", name="忽略厂", publisher="IgnoredPub Inc", genre="Strategy"),
        ])
        await db.commit()
    # 忽略名单（与 /gaps 同口径）：app_id 粒度 + publisher 粒度（后端归一成 squash 键）
    await client.post("/api/publishers/ignores", json={"kind": "app_id", "raw_value": "x_ig_app"})
    await client.post("/api/publishers/ignores", json={"kind": "publisher", "raw_value": "IgnoredPub Inc"})

    leads = (await client.get("/api/publishers/download-leads")).json()
    assert {l["app_id"] for l in leads} == {"lead1", "lead2"}
    assert len([l for l in leads if l["app_id"] == "lead1"]) == 1  # 跨市场收敛成一行
    by_app = {l["app_id"]: l for l in leads}
    assert by_app["lead1"]["genre"] == "Strategy"  # genre 缺失的 ios 行经回填仍拿到
    assert by_app["lead2"]["publisher"] == "NewCo B"


@pytest.mark.asyncio
async def test_download_leads_excludes_rows_attributed_to_built_entity(client):
    """存档 is_slg 是检出时点快照：app 先在 free 榜检出（is_slg=false 落库），主体随后建档
    并 pin 该 app_id —— 存档不回写仍是 false。下载榜信号必须与新品监测页同口径**读时归属**，
    把已归属已建档主体的 app 排除掉，否则同一款已建档产品会永远赖在「待建档新厂线索」里
    （prod 真实案例：com.more.lastshelter.gp / 龙创悦动 IM30）。app_id pin 与 publisher
    alias 两条归属路径都要排除。"""
    from app.database import AsyncSessionLocal
    from app.models.newcomer import MarketNewcomerLog

    def mk(**kw):
        base = dict(country="US", platform="android", as_of="2026-06-29",
                    chart_type="free", is_slg=False, is_reentry=False, genre="Strategy")
        base.update(kw)
        return MarketNewcomerLog(**base)

    async with AsyncSessionLocal() as db:
        db.add_all([
            # 已 pin 到主体的 app（存档 is_slg=false 是检出时点旧值）→ 必须排除
            mk(app_id="com.more.lastshelter.gp", name="Last Shelter", publisher="LAST ORIGIN STUDIO LIMITED", rank=44),
            # publisher 命中主体 alias（app_id 未 pin）→ 也必须排除
            mk(app_id="pinless.alias.app", name="别名命中", publisher="LAST ORIGIN STUDIO LIMITED", rank=50),
            # 真·未建档新厂线索 → 保留
            mk(app_id="genuine.lead", name="真新厂", publisher="Brand New Co", rank=60),
        ])
        await db.commit()
    # 建档：pin app_id + alias，复刻 prod 的「龙创悦动 IM30」配置
    await client.post("/api/publishers/", json={
        "name": "龙创悦动 IM30",
        "aliases": [{"keyword": "last origin studio"}],
        "app_ids": [{"app_id": "com.more.lastshelter.gp"}],
    })

    leads = (await client.get("/api/publishers/download-leads")).json()
    app_ids = {l["app_id"] for l in leads}
    assert "com.more.lastshelter.gp" not in app_ids  # app_id pin 反解 → 已归属，排除
    assert "pinless.alias.app" not in app_ids         # publisher alias 反解 → 已归属，排除
    assert "genuine.lead" in app_ids                  # 未建档真线索保留


@pytest.mark.asyncio
async def test_sibling_dedup_collapses_ios_android_same_game(client):
    """同 publisher + 名字 prefix 匹配的 iOS+Android 同款 → 合并为 1 个 product，
    收入/下载求和；product_count + top_products + /products 三处口径一致。"""
    today = _today()
    await _seed_rankings([
        # 同款 Whiteout Survival iOS + Android（US 名一致）
        ("ios.whiteout", today, 1, 300, 100.0, "US", "ios", "Whiteout Survival", "Century Games Pte. Ltd."),
        ("gp.whiteout",  today, 2, 200,  80.0, "US", "android", "Whiteout Survival", "Century Games PTE. LTD."),
        # 另一款 Kingshot 仅 iOS（KR 上榜，US 没拉到 → 没有 US 优先名兜底）
        ("ios.kingshot", today, 3, 100,  50.0, "KR", "ios", "Kingshot", "Century Games Pte. Ltd."),
        # 无关游戏
        ("other.app",    today, 4,  10,   5.0, "US", "ios", "无关",     "Other Studio"),
    ])
    eid = (await client.post("/api/publishers/", json={
        "name": "点点测试", "aliases": [{"keyword": "century games"}],
    })).json()["id"]
    # /products: 应只剩 2 行（Whiteout 合并 + Kingshot 独立）
    prods = (await client.get(f"/api/publishers/{eid}/products")).json()
    assert len(prods) == 2, f"expected 2 deduped products, got {len(prods)}: {[p['name'] for p in prods]}"
    by_name = {p["name"]: p for p in prods}
    assert "Whiteout Survival" in by_name
    # 合并后收入/下载求和
    assert by_name["Whiteout Survival"]["revenue"] == 180.0  # 100 + 80
    assert by_name["Whiteout Survival"]["downloads"] == 500  # 300 + 200
    assert "Kingshot" in by_name
    assert by_name["Kingshot"]["revenue"] == 50.0
    # list 端点 product_count + top_products 也是去重后口径
    lst = (await client.get("/api/publishers/")).json()
    e = next(x for x in lst if x["id"] == eid)
    assert e["product_count"] == 2
    top_names = [p["name"] for p in e["top_products"]]
    assert "Whiteout Survival" in top_names
    assert "Kingshot" in top_names


@pytest.mark.asyncio
async def test_sibling_dedup_preserves_cjk_only_independent(client):
    """纯 CJK 本地化名（normalize 后为空字符串）不参与 sibling 合并，保留为独立组。
    这是兼容性保底——避免把无关游戏因都是 CJK 名而误合。"""
    today = _today()
    await _seed_rankings([
        ("a.cjk", today, 1, 100, 50.0, "JP", "ios", "游戏一", "Tako Games"),
        ("b.cjk", today, 2, 100, 50.0, "JP", "ios", "游戏二", "Tako Games"),
    ])
    eid = (await client.post("/api/publishers/", json={
        "name": "CJK 测试", "aliases": [{"keyword": "tako"}],
    })).json()["id"]
    prods = (await client.get(f"/api/publishers/{eid}/products")).json()
    assert len(prods) == 2  # 不合并


@pytest.mark.asyncio
async def test_sibling_dedup_merges_across_publisher_string_variants(client):
    """**entity scope** 内的跨 publisher 字符串同款合并：
    同一家公司不同法人/分公司常用不同 publisher 字符串发 iOS/Android（"TOP GAMES INC." vs
    "TG Inc."、"IGG SINGAPORE PTE. LTD." vs "IGG.COM"、"InnoGames GmbH" vs "InnoGames"），
    但它们的 alias 已全归到同一 entity。`_dedup_siblings` 不再做 publisher 字符串等价检查，
    完全按名字 prefix ≥5 字符在 entity 内合并，把这些「真同款」收编。
    回归 2026-06 上线的 sibling dedup 在线上 26 个 SLG 主体里漏合 ~46 条产品 row 的问题。"""
    today = _today()
    await _seed_rankings([
        # Evony iOS（"TOP GAMES INC."）+ Android（"TG Inc."），都属同一家
        ("ios.evony", today, 1, 200, 100.0, "US", "ios",     "Evony",                    "TOP GAMES INC."),
        ("gp.evony",  today, 2, 150,  80.0, "US", "android", "Evony: The King's Return", "TG Inc."),
        # 另一款独立游戏（确保不被误合）
        ("ios.other", today, 3,  50,  20.0, "US", "ios",     "Valkyrie Raid",            "TG Inc."),
    ])
    eid = (await client.post("/api/publishers/", json={
        "name": "Top Games",
        "aliases": [{"keyword": "top games"}, {"keyword": "tg inc"}],
    })).json()["id"]
    prods = (await client.get(f"/api/publishers/{eid}/products")).json()
    by_name = {p["name"]: p for p in prods}
    # Evony iOS + Android 应合成 1 行（而非 2 行）
    assert len(prods) == 2, f"expected 2 deduped products (Evony merged + Valkyrie), got {len(prods)}: {[p['name'] for p in prods]}"
    # 合并后偏好「最长含 Latin 名」→ "Evony: The King's Return"
    assert "Evony: The King's Return" in by_name
    assert by_name["Evony: The King's Return"]["revenue"] == 180.0  # 100 + 80
    assert by_name["Evony: The King's Return"]["downloads"] == 350  # 200 + 150
    assert "Valkyrie Raid" in by_name
    # list 端点 product_count 也对齐
    lst = (await client.get("/api/publishers/")).json()
    e = next(x for x in lst if x["id"] == eid)
    assert e["product_count"] == 2


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
async def test_gaps_squash_fallback_attributes_glued_publisher(client):
    """连写发行商 "Topgames.Inc" 应被 alias "top games" 的 squash 回退归属 → 不进缺口。
    子序列匹配（["topgames","inc"] vs ["top","games"]）配不上，靠 corp_squash 兜底。"""
    today = _today()
    await _seed_rankings([
        ("glue.1", today, 1, 200, 90.0, "US", "ios", "Evony", "Topgames.Inc"),
        # 真漏网厂（squash 也配不上任何 alias）
        ("gap.x", today, 2, 100, 40.0, "US", "ios", "漏网", "Mystery Studio"),
    ])
    await client.post("/api/publishers/", json={
        "name": "Top Games", "aliases": [{"keyword": "top games"}],
    })
    gaps = (await client.get("/api/publishers/gaps")).json()
    pubs = {g["publisher"] for g in gaps}
    assert "Topgames.Inc" not in pubs   # squash 回退归属
    assert "Mystery Studio" in pubs


@pytest.mark.asyncio
async def test_ignore_publisher_excludes_from_gaps_and_restores(client):
    """忽略名单（publisher 粒度）：忽略后该发行商不再进缺口；删除忽略后恢复。
    publisher 串归一成 corp_squash 键存储 → "Niantic, Inc." 与 "Niantic Inc" 同条。"""
    today = _today()
    await _seed_rankings([
        ("nia.1", today, 1, 500, 300.0, "US", "ios", "Pokemon GO", "Niantic, Inc."),
        ("keep.1", today, 2, 100, 40.0, "US", "ios", "保留", "Real SLG Studio"),
    ])
    # 初始：两者都是缺口
    gaps = {g["publisher"] for g in (await client.get("/api/publishers/gaps")).json()}
    assert "Niantic, Inc." in gaps and "Real SLG Studio" in gaps

    # 忽略 Niantic（传原始串，后端归一）
    r = await client.post("/api/publishers/ignores", json={
        "kind": "publisher", "raw_value": "Niantic, Inc.", "note": "AR 游戏，非 SLG",
    })
    assert r.status_code == 201
    ig = r.json()
    assert ig["kind"] == "publisher"
    assert ig["value"] == "niantic"      # corp_squash 去掉 inc
    assert ig["label"] == "Niantic, Inc."

    # 缺口里 Niantic 没了，但保留厂还在
    gaps = {g["publisher"] for g in (await client.get("/api/publishers/gaps")).json()}
    assert "Niantic, Inc." not in gaps
    assert "Real SLG Studio" in gaps

    # 大小写/标点不同的同厂串也被同一条 squash 命中（不会重新冒出来）
    await _seed_rankings([("nia.2", today, 5, 50, 20.0, "JP", "android", "Pikmin Bloom", "NIANTIC INC")])
    gaps = {g["publisher"] for g in (await client.get("/api/publishers/gaps")).json()}
    assert "NIANTIC INC" not in gaps

    # 列表端点能看到这条
    lst = (await client.get("/api/publishers/ignores")).json()
    assert any(x["value"] == "niantic" for x in lst)

    # 删除（恢复）→ Niantic 重新进缺口
    r = await client.delete(f"/api/publishers/ignores/{ig['id']}")
    assert r.status_code == 200
    gaps = {g["publisher"] for g in (await client.get("/api/publishers/gaps")).json()}
    assert "Niantic, Inc." in gaps


@pytest.mark.asyncio
async def test_ignore_app_id_granularity_and_idempotent(client):
    """忽略 app_id 粒度：只剔某一款 app，同发行商其它 app 仍进缺口。重复忽略幂等。"""
    today = _today()
    await _seed_rankings([
        ("big.a", today, 1, 100, 50.0, "US", "ios", "非SLG单品", "Big Multi Studio"),
        ("big.b", today, 2, 100, 60.0, "US", "ios", "另一单品", "Big Multi Studio"),
    ])
    # 忽略 big.a 这一个 app
    r = await client.post("/api/publishers/ignores", json={"kind": "app_id", "raw_value": "big.a"})
    assert r.status_code == 201
    first_id = r.json()["id"]
    assert r.json()["value"] == "big.a"

    # 幂等：再忽略同一个 → 返回同一条，不报错
    r2 = await client.post("/api/publishers/ignores", json={"kind": "app_id", "raw_value": "big.a"})
    assert r2.status_code == 201
    assert r2.json()["id"] == first_id

    # big.a 被剔，但同发行商仍因 big.b 进缺口（app_count=1，代表 app=big.b）
    gaps = {g["publisher"]: g for g in (await client.get("/api/publishers/gaps")).json()}
    assert "Big Multi Studio" in gaps
    assert gaps["Big Multi Studio"]["app_count"] == 1
    assert gaps["Big Multi Studio"]["top_app"]["app_id"] == "big.b"


@pytest.mark.asyncio
async def test_ignore_rejects_empty_publisher_squash(client):
    """publisher 名归一后为空（纯法人后缀）→ 422，不让存一条会误吞所有空 squash 的脏数据。"""
    r = await client.post("/api/publishers/ignores", json={"kind": "publisher", "raw_value": "Inc."})
    assert r.status_code == 422


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
