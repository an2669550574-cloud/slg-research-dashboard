"""App Store 开发者清单 diff（厂商新品 P2）。

核心验证：
- 首次同步全量落库标 is_baseline=True，不报"新"；二次同步出现的新 track_id 才是新上架
- /api/newcomers/appstore 只返回非基线行，带主体名与账号 label
- 账号 CRUD：artist_id 全局唯一（409）、删账号连带删清单
- 手动 sync 端点：mock 模式空跑；真实模式走 monkeypatch 的 fetch，单账号失败不拖垮整批
- 中文 app 名夹具（CJK 纪律）
"""
import pytest


async def _mk_entity_with_artist(client, name="江娱互动测试", artist_id="1717022676", label="River Game HK"):
    r = await client.post("/api/publishers/", json={"name": name})
    assert r.status_code == 201
    entity = r.json()
    r2 = await client.post(f"/api/publishers/{entity['id']}/itunes-artists",
                           json={"artist_id": artist_id, "label": label})
    assert r2.status_code == 201
    return entity, r2.json()


def _app(track_id, name, bundle_id=None, release_date="2026-06-01", storefronts=None):
    r = {
        "wrapperType": "software", "trackId": track_id, "trackName": name,
        "bundleId": bundle_id or f"com.test.{track_id}",
        "releaseDate": f"{release_date}T00:00:00Z",
        "trackViewUrl": f"https://apps.apple.com/us/app/id{track_id}",
        # 免费 lookup 同响应里的展示/详情字段（零增量 ST）
        "artworkUrl512": f"https://is1-ssl.mzstatic.com/image/{track_id}/512x512bb.jpg",
        "genres": ["Games", "Strategy", "Simulation"],
        "primaryGenreName": "Games",
        "averageUserRating": 4.6,
        "userRatingCount": 12345,
        "formattedPrice": "Free",
        "description": f"《{name}》——史诗级策略大作，集结英雄共抗严寒。",
        "screenshotUrls": [f"https://is1-ssl.mzstatic.com/image/{track_id}/shot{i}.jpg" for i in range(7)],
        "languageCodesISO2A": ["EN", "ZH", "JA", "KO"],
    }
    if storefronts is not None:
        r["_seen_storefronts"] = set(storefronts)
    return r


def _counts(result: dict) -> dict:
    """忽略 expanded_rows 明细、enriched（展示字段自愈）与 radar_newcomers（P1-1 富化影子行
    收集，非 diff 计数），只比 diff 计数。"""
    return {k: v for k, v in result.items()
            if k not in ("expanded_rows", "enriched", "radar_newcomers")}


@pytest.mark.asyncio
async def test_baseline_then_new_release(client):
    """首次同步=基线不报新；第二次出现的新 track_id = 新上架。"""
    from app.services.itunes_releases import ingest_artist_apps
    _entity, artist = await _mk_entity_with_artist(client)

    r1 = await ingest_artist_apps(artist["id"], [
        _app(100, "口袋奇兵"), _app(101, "Top Heroes: Kingdom Saga"),
    ])
    assert _counts(r1) == {"baselined": 2, "new_apps": 0, "backfilled_old": 0, "expanded": 0}

    r2 = await ingest_artist_apps(artist["id"], [
        _app(100, "口袋奇兵"), _app(101, "Top Heroes: Kingdom Saga"),
        _app(102, "测试新游：星际远征", storefronts={"ph", "ca"}),
    ])
    assert _counts(r2) == {"baselined": 0, "new_apps": 1, "backfilled_old": 0, "expanded": 0}

    # 端点只报非基线行
    resp = await client.get("/api/newcomers/appstore")
    body = resp.json()
    assert [i["name"] for i in body["items"]] == ["测试新游：星际远征"]
    item = body["items"][0]
    assert item["entity_name"] == "江娱互动测试"
    assert item["artist_label"] == "River Game HK"
    assert item["release_date"] == "2026-06-01"
    # 免费 iTunes 展示字段随同响应落库并回显；genre 取 genres[] 第一个非 "Games" 子品类
    assert item["genre"] == "Strategy"
    assert item["rating"] == 4.6
    assert item["rating_count"] == 12345
    assert item["price"] == "Free"
    assert item["artwork_url"].endswith("512x512bb.jpg")
    # 多区可见性 + 检出详情（截图截到 5 张、CJK 描述原样）
    assert item["storefronts"] == ["ph", "ca"]
    assert "史诗级策略大作" in item["description"]
    assert len(item["screenshots"]) == 5
    assert body["artists_total"] == 1 and body["artists_synced"] == 1


@pytest.mark.asyncio
async def test_ingest_idempotent(client):
    """同一份清单重复灌 → 不重复落库。"""
    from app.services.itunes_releases import ingest_artist_apps
    _entity, artist = await _mk_entity_with_artist(client)
    await ingest_artist_apps(artist["id"], [_app(200, "野蛮时代")])
    r = await ingest_artist_apps(artist["id"], [_app(200, "野蛮时代")])
    assert _counts(r) == {"baselined": 0, "new_apps": 0, "backfilled_old": 0, "expanded": 0}


@pytest.mark.asyncio
async def test_existing_row_display_fields_selfheal(client):
    """已存在行的展示字段自愈：基线行原本无 artwork，下一轮 lookup 带 artwork → 回填，
    且只填空、不覆盖已有值（模拟早期无 artwork 基线的历史缺口逐轮补齐）。"""
    from app.services.itunes_releases import ingest_artist_apps
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.publisher import PublisherItunesApp
    _entity, artist = await _mk_entity_with_artist(client)

    # 首轮：人为去掉 artwork/genre（模拟早期基线行）
    bare = _app(210, "口袋奇兵")
    bare.pop("artworkUrl512")
    bare["genres"] = ["Games"]
    bare.pop("primaryGenreName", None)  # 无子品类 + 无主品类 → genre 落 None
    bare["averageUserRating"] = 3.1  # 哨兵：已有值，次轮 4.6 不应覆盖
    await ingest_artist_apps(artist["id"], [bare])
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(PublisherItunesApp).where(
            PublisherItunesApp.track_id == "210"))).scalar_one()
        assert row.artwork_url is None and row.genre is None
        assert row.rating == 3.1

    # 次轮：同 track_id 带齐展示字段 → artwork/genre 回填，enriched 计数 > 0
    r = await ingest_artist_apps(artist["id"], [_app(210, "口袋奇兵")])
    assert _counts(r) == {"baselined": 0, "new_apps": 0, "backfilled_old": 0, "expanded": 0}
    assert r["enriched"] >= 2  # 至少 artwork_url + genre
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(PublisherItunesApp).where(
            PublisherItunesApp.track_id == "210"))).scalar_one()
        assert row.artwork_url and row.artwork_url.endswith("512x512bb.jpg")
        assert row.genre == "Strategy"
        assert row.rating == 3.1  # 原有值不被覆盖（次轮 4.6 被 fill-only 跳过）


@pytest.mark.asyncio
async def test_artist_crud_unique_and_cascade(client):
    """artist_id 全局唯一（409）；删账号连带删清单快照。"""
    from app.services.itunes_releases import ingest_artist_apps
    entity, artist = await _mk_entity_with_artist(client)

    # 另一主体挂同一 artist_id → 409
    r = await client.post("/api/publishers/", json={"name": "另一主体"})
    other = r.json()
    dup = await client.post(f"/api/publishers/{other['id']}/itunes-artists",
                            json={"artist_id": "1717022676"})
    assert dup.status_code == 409

    # 非数字 artist_id → 422
    bad = await client.post(f"/api/publishers/{other['id']}/itunes-artists",
                            json={"artist_id": "river-game"})
    assert bad.status_code == 422

    await ingest_artist_apps(artist["id"], [_app(300, "胜利之吻")])
    # 列表端点回显账号
    lst = await client.get("/api/publishers/")
    me = next(e for e in lst.json() if e["id"] == entity["id"])
    assert me["itunes_artists"][0]["artist_id"] == "1717022676"
    assert me["itunes_artists"][0]["last_synced_at"] is not None

    # 删账号 → 清单一并清空
    rd = await client.delete(f"/api/publishers/{entity['id']}/itunes-artists/{artist['id']}")
    assert rd.status_code == 200
    resp = await client.get("/api/newcomers/appstore")
    assert resp.json()["artists_total"] == 0


@pytest.mark.asyncio
async def test_sync_mock_mode_noop(client):
    """mock 模式下 sync 不出外网、直接空跑。"""
    r = await client.post("/api/newcomers/appstore/sync")
    assert r.status_code == 200
    assert r.json()["synced"] == 0


@pytest.mark.asyncio
async def test_sync_real_mode_with_failure_isolation(client, monkeypatch):
    """真实模式：逐账号拉清单；单账号失败计入 failed、不拖垮其他账号。"""
    import importlib
    svc = importlib.import_module("app.services.itunes_releases")
    from app.config import settings

    await _mk_entity_with_artist(client, name="壳木测试", artist_id="111", label="A")
    await _mk_entity_with_artist(client, name="元趣测试", artist_id="222", label="B")

    async def fake_fetch(artist_id, country="us"):
        if artist_id == "111":
            raise RuntimeError("boom")
        return [_app(900, "测试上架：寒霜纪元")]

    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(svc, "fetch_artist_apps", fake_fetch)
    monkeypatch.setattr(svc, "_POLITE_DELAY_S", 0)

    summary = await svc.sync_itunes_releases()
    assert summary["synced"] == 1 and summary["failed"] == 1
    assert summary["baselined"] == 1 and summary["new_apps"] == 0


@pytest.mark.asyncio
async def test_multi_storefront_merge(client, monkeypatch):
    """多区拉取按 trackId 合并、_seen_storefronts 取并集；单区失败不拖垮账号。"""
    import importlib
    svc = importlib.import_module("app.services.itunes_releases")
    from app.config import settings

    calls: list[str] = []

    async def fake_fetch(artist_id, country="us"):
        calls.append(country)
        if country == "au":
            raise RuntimeError("region boom")  # 单区失败 → 只丢该区可见性
        if country == "us":
            return [_app(500, "寒霜纪元国际服")]
        return [_app(500, "寒霜纪元国际服"), _app(501, "软启动新游：冰原王座")]

    monkeypatch.setattr(settings, "ITUNES_RELEASES_STOREFRONTS", "us,ph,ca,au,sg")
    monkeypatch.setattr(svc, "fetch_artist_apps", fake_fetch)
    monkeypatch.setattr(svc, "_POLITE_DELAY_S", 0)

    merged = await svc.fetch_artist_apps_multi("999")
    assert calls == ["us", "ph", "ca", "au", "sg"]
    by_tid = {str(r["trackId"]): r for r in merged}
    assert set(by_tid) == {"500", "501"}
    assert by_tid["500"]["_seen_storefronts"] == {"us", "ph", "ca", "sg"}
    assert by_tid["501"]["_seen_storefronts"] == {"ph", "ca", "sg"}  # 美区不可见 = 软启动

    # 全区失败 → 整账号抛（由 sync 循环计 failed）
    async def all_fail(artist_id, country="us"):
        raise RuntimeError("total boom")
    monkeypatch.setattr(svc, "fetch_artist_apps", all_fail)
    with pytest.raises(RuntimeError):
        await svc.fetch_artist_apps_multi("999")


@pytest.mark.asyncio
async def test_old_release_silently_baselined(client):
    """基线后新见到、但 release_date 太老的 app → 静默入基线不报新。

    场景：新增扫描区首轮捞出历史区域限定 app / 老包重新上架。"""
    from app.services.itunes_releases import ingest_artist_apps
    _entity, artist = await _mk_entity_with_artist(client)
    await ingest_artist_apps(artist["id"], [_app(600, "口袋奇兵")])

    r = await ingest_artist_apps(artist["id"], [
        _app(600, "口袋奇兵"),
        _app(601, "上古区域限定版", release_date="2022-01-01", storefronts={"ph"}),
        _app(602, "真新品：极地征服", release_date="2026-06-10", storefronts={"ph"}),
    ])
    assert _counts(r) == {"baselined": 0, "new_apps": 1, "backfilled_old": 1, "expanded": 0}

    resp = await client.get("/api/newcomers/appstore")
    names = [i["name"] for i in resp.json()["items"]]
    assert names == ["真新品：极地征服"]  # 老包不出现在雷达


@pytest.mark.asyncio
async def test_established_app_silently_baselined_when_no_release_date(client):
    """release_date 缺失（GP 侧常态，页面拿不到）时用评价数判老：评价数超阈值的老 app
    静默入基线，评价数缺失/低的真软启动仍报新。治 GP 开发者页分页、首同步漏抓的老游戏
    下轮现身被误报"新上架"（真实样本：EasyTech 的 World Conqueror 2，6.5 万评价）。"""
    from app.services.itunes_releases import ingest_artist_apps
    _entity, artist = await _mk_entity_with_artist(client)
    await ingest_artist_apps(artist["id"], [_app(800, "口袋奇兵")])

    def _gp(track_id, name, rating_count):
        r = _app(track_id, name, storefronts={"gp"})
        r.pop("releaseDate")            # GP 详情页拿不到上架日 → release_date NULL
        r["userRatingCount"] = rating_count
        return r

    r = await ingest_artist_apps(artist["id"], [
        _app(800, "口袋奇兵"),
        _gp(801, "World Conqueror 2", 65521),  # 老品：评价数超阈值 → 静默入基线
        _gp(802, "真软启动：极地征服", 3),        # 真新品：评价数极低 → 报新
        _gp(803, "评价数缺失新游", None),         # 评价数缺失 → 按新处理不丢信号
    ])
    assert _counts(r) == {"baselined": 0, "new_apps": 2, "backfilled_old": 1, "expanded": 0}

    resp = await client.get("/api/newcomers/appstore")
    names = {i["name"] for i in resp.json()["items"]}
    assert names == {"真软启动：极地征服", "评价数缺失新游"}  # 老品 World Conqueror 2 不出现


@pytest.mark.asyncio
async def test_recent_ios_app_with_high_ratings_still_new(client):
    """回归护栏：iOS 有真实 release_date 时评价数守卫不介入——首月冲高评价的真爆款新游
    （release_date 近 + 评价数超阈值）仍报"新上架"，不被误当老品静默掉。"""
    from app.services.itunes_releases import ingest_artist_apps
    _entity, artist = await _mk_entity_with_artist(client)
    await ingest_artist_apps(artist["id"], [_app(810, "口袋奇兵")])

    hit = _app(811, "爆款新游：寒霜纪元", release_date="2026-06-20")
    hit["userRatingCount"] = 50000  # 上架仅 11 天却冲到 5 万评价，仍是新品
    r = await ingest_artist_apps(artist["id"], [_app(810, "口袋奇兵"), hit])
    assert _counts(r) == {"baselined": 0, "new_apps": 1, "backfilled_old": 0, "expanded": 0}


@pytest.mark.asyncio
async def test_storefront_expansion_detected(client):
    """非基线行新增可见区 = 扩区上线；基线行扩区只刷新不报。"""
    from app.services.itunes_releases import ingest_artist_apps
    _entity, artist = await _mk_entity_with_artist(client)
    # 基线：一款 us 老 app
    await ingest_artist_apps(artist["id"], [_app(700, "野蛮时代", storefronts={"us"})])
    # 新品在 ph 软启动
    r1 = await ingest_artist_apps(artist["id"], [
        _app(700, "野蛮时代", storefronts={"us"}),
        _app(701, "软启动：冻土黎明", storefronts={"ph"}),
    ])
    assert _counts(r1)["new_apps"] == 1

    # 下一轮：新品扩到 us+ca；基线 app 也"扩"到 ph（不报）
    r2 = await ingest_artist_apps(artist["id"], [
        _app(700, "野蛮时代", storefronts={"us", "ph"}),
        _app(701, "软启动：冻土黎明", storefronts={"ph", "us", "ca"}),
    ])
    assert _counts(r2) == {"baselined": 0, "new_apps": 0, "backfilled_old": 0, "expanded": 1}
    assert len(r2["expanded_rows"]) == 1
    _row_id, added = r2["expanded_rows"][0]
    assert added == ["us", "ca"]

    # 端点回显合并后的可见区（us 排最前）
    resp = await client.get("/api/newcomers/appstore")
    item = next(i for i in resp.json()["items"] if i["track_id"] == "701")
    assert item["storefronts"] == ["us", "ph", "ca"]


@pytest.mark.asyncio
async def test_resolve_artist_multi_storefront_fallback(monkeypatch):
    """反解逐区兜底：app 区域限定（只在 jp 区可见）→ us 查空、回退 jp 命中开发者账号。
    治「日韩限定 SLG 的 iOS app 在美区失明 → 雷达覆盖建议反解不出」（实测 gumi/星辉/英雄互娱）。"""
    import app.services.itunes_releases as svc

    class _Resp:
        def __init__(self, results):
            self._results = results

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": self._results}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            if (params or {}).get("country") == "jp":  # 只有 jp 区能看到该 app
                return _Resp([{"wrapperType": "software", "artistId": 532518920,
                               "artistName": "gumi Inc.", "trackName": "クリスタル オブ リユニオン"}])
            return _Resp([])  # us 等其它区 resultCount=0

    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda *a, **k: _Client())
    monkeypatch.setattr(svc, "_ARTIST_RESOLVE_DELAY_S", 0)  # 测试不真 sleep

    got = await svc.resolve_artist_for_app("993529737")
    assert got == {"artist_id": "532518920", "artist_name": "gumi Inc.",
                   "app_name": "クリスタル オブ リユニオン"}

    # 全区都查不到 → None（不误造账号）
    class _Empty(_Client):
        async def get(self, url, params=None):
            return _Resp([])

    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda *a, **k: _Empty())
    assert await svc.resolve_artist_for_app("993529737") is None
