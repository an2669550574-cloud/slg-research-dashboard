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


def _app(track_id, name, bundle_id=None, release_date="2026-06-01"):
    return {
        "wrapperType": "software", "trackId": track_id, "trackName": name,
        "bundleId": bundle_id or f"com.test.{track_id}",
        "releaseDate": f"{release_date}T00:00:00Z",
        "trackViewUrl": f"https://apps.apple.com/us/app/id{track_id}",
        # 免费 lookup 同响应里的展示字段（零增量 ST）
        "artworkUrl512": f"https://is1-ssl.mzstatic.com/image/{track_id}/512x512bb.jpg",
        "genres": ["Games", "Strategy", "Simulation"],
        "primaryGenreName": "Games",
        "averageUserRating": 4.6,
        "userRatingCount": 12345,
        "formattedPrice": "Free",
    }


@pytest.mark.asyncio
async def test_baseline_then_new_release(client):
    """首次同步=基线不报新；第二次出现的新 track_id = 新上架。"""
    from app.services.itunes_releases import ingest_artist_apps
    _entity, artist = await _mk_entity_with_artist(client)

    r1 = await ingest_artist_apps(artist["id"], [
        _app(100, "口袋奇兵"), _app(101, "Top Heroes: Kingdom Saga"),
    ])
    assert r1 == {"baselined": 2, "new_apps": 0}

    r2 = await ingest_artist_apps(artist["id"], [
        _app(100, "口袋奇兵"), _app(101, "Top Heroes: Kingdom Saga"),
        _app(102, "测试新游：星际远征"),
    ])
    assert r2 == {"baselined": 0, "new_apps": 1}

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
    assert body["artists_total"] == 1 and body["artists_synced"] == 1


@pytest.mark.asyncio
async def test_ingest_idempotent(client):
    """同一份清单重复灌 → 不重复落库。"""
    from app.services.itunes_releases import ingest_artist_apps
    _entity, artist = await _mk_entity_with_artist(client)
    await ingest_artist_apps(artist["id"], [_app(200, "野蛮时代")])
    r = await ingest_artist_apps(artist["id"], [_app(200, "野蛮时代")])
    assert r == {"baselined": 0, "new_apps": 0}


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

    async def fake_fetch(artist_id):
        if artist_id == "111":
            raise RuntimeError("boom")
        return [_app(900, "测试上架：寒霜纪元")]

    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(svc, "fetch_artist_apps", fake_fetch)
    monkeypatch.setattr(svc, "_POLITE_DELAY_S", 0)

    summary = await svc.sync_itunes_releases()
    assert summary["synced"] == 1 and summary["failed"] == 1
    assert summary["baselined"] == 1 and summary["new_apps"] == 0
