"""GP 侧雷达（Google Play 开发者页清单 diff）。

核心验证：
- 开发者页解析包名清单；详情页 JSON-LD 解析（中文 app 名，CJK 纪律）
- platform='gp' 账号挂载（名称型 id 放行）；'ios' 仍强制纯数字
- diff 语义复用：首次同步入基线不报新，二次出现的新包名才是新上架
- /api/newcomers/appstore 带 platform 字段；GP 行 storefronts=['gp']
- sync：mock 模式空跑；详情页失败降级为仅包名记录不丢信号
"""
import json

import pytest

from app.services.gp_releases import (
    developer_page_url, parse_app_detail, parse_developer_packages,
)

_DEV_HTML = """
<html><body>
<a href="/store/apps/details?id=com.gamespark.mykingdom.gp">Top King</a>
<a href="/store/apps/details?id=com.gamespark.topking.gp">Top Lords</a>
<a href="/store/apps/details?id=com.gamespark.mykingdom.gp">dup</a>
</body></html>
"""

_APP_HTML = """
<html><head>
<meta property="og:image" content="https://play-lh.googleusercontent.com/og.png">
<script type="application/ld+json" nonce="x">
{"name": "王国崛起：酒馆传说", "description": "经营你的酒馆，招募英雄。",
 "applicationCategory": "GAME_STRATEGY",
 "image": "https://play-lh.googleusercontent.com/icon512.png",
 "aggregateRating": {"ratingValue": 4.3, "ratingCount": 2141},
 "offers": [{"price": "0", "priceCurrency": "USD"}]}
</script>
</head><body></body></html>
"""


def test_developer_page_url_forms():
    assert developer_page_url("GAME SPARK").endswith("/store/apps/developer?id=GAME+SPARK")
    assert developer_page_url("5700313618786177705").endswith("/store/apps/dev?id=5700313618786177705")


def test_parse_developer_packages_dedup_ordered():
    assert parse_developer_packages(_DEV_HTML) == [
        "com.gamespark.mykingdom.gp", "com.gamespark.topking.gp",
    ]


def test_parse_app_detail_jsonld_cjk():
    r = parse_app_detail(_APP_HTML, "com.gamespark.mykingdom.gp")
    assert r["trackId"] == "com.gamespark.mykingdom.gp"
    assert r["trackName"] == "王国崛起：酒馆传说"
    assert r["genres"] == ["Strategy"]
    assert r["averageUserRating"] == 4.3
    assert r["userRatingCount"] == 2141
    assert r["formattedPrice"] == "Free"
    assert r["artworkUrl512"] == "https://play-lh.googleusercontent.com/icon512.png"
    assert r["_seen_storefronts"] == {"gp"}


def test_parse_app_detail_prefers_full_description():
    """正文容器（data-g-id=description）比 JSON-LD 短标语长 → 用正文，标签清洗 + 反转义。"""
    html = _APP_HTML.replace(
        "</head><body></body></html>",
        '</head><body>'
        '<div data-g-id="description">'
        '经营你的酒馆，招募传奇英雄，<br>在七国之地建立你的王朝 &amp; 称霸维斯特洛。'
        '这是一款史诗级 4X 策略战争手游，深度玩法应有尽有。'
        '</div></body></html>')
    r = parse_app_detail(html, "com.gamespark.mykingdom.gp")
    assert "建立你的王朝 & 称霸维斯特洛" in r["description"]  # 反转义 + 取到正文
    assert "<" not in r["description"] and ">" not in r["description"]  # 标签已清
    assert len(r["description"]) > len("经营你的酒馆，招募英雄。")  # 比 JSON-LD 短标语长


def test_parse_app_detail_falls_back_to_jsonld_short():
    """无正文容器 → 回退 JSON-LD 短描述（不回归）。"""
    r = parse_app_detail(_APP_HTML, "com.gamespark.mykingdom.gp")
    assert r["description"] == "经营你的酒馆，招募英雄。"


def test_parse_app_detail_degrades_to_package_only():
    r = parse_app_detail("<html><body>no structured data</body></html>", "com.test.无结构")
    assert r["trackId"] == "com.test.无结构"
    assert r["trackName"] == "com.test.无结构"  # 降级：包名即身份


async def _mk_entity_with_gp_account(client, name="GAME SPARK 测试", dev_id="GAME SPARK"):
    r = await client.post("/api/publishers/", json={"name": name})
    assert r.status_code == 201
    entity = r.json()
    r2 = await client.post(f"/api/publishers/{entity['id']}/itunes-artists",
                           json={"artist_id": dev_id, "platform": "gp", "label": "GP 开发者页"})
    assert r2.status_code == 201, r2.text
    assert r2.json()["platform"] == "gp"
    return entity, r2.json()


@pytest.mark.asyncio
async def test_gp_account_mount_and_ios_still_numeric(client):
    entity, account = await _mk_entity_with_gp_account(client)
    # ios 平台仍强制纯数字
    r = await client.post(f"/api/publishers/{entity['id']}/itunes-artists",
                          json={"artist_id": "NOT NUMERIC", "platform": "ios"})
    assert r.status_code == 422
    # gp 账号 id 全局唯一
    r = await client.post(f"/api/publishers/{entity['id']}/itunes-artists",
                          json={"artist_id": "GAME SPARK", "platform": "gp"})
    assert r.status_code == 409


def _gp_record(pkg, name):
    return {
        "wrapperType": "software", "trackId": pkg, "trackName": name,
        "bundleId": pkg, "trackViewUrl": f"https://play.google.com/store/apps/details?id={pkg}",
        "genres": ["Strategy"], "formattedPrice": "Free",
        "description": f"《{name}》——中世纪奇幻挂机 SLG。",
        "_seen_storefronts": {"gp"},
    }


@pytest.mark.asyncio
async def test_gp_baseline_then_new_release(client):
    entity, account = await _mk_entity_with_gp_account(client)
    from app.services.itunes_releases import ingest_artist_apps

    r1 = await ingest_artist_apps(account["id"], [
        _gp_record("com.gamespark.mykingdom.gp", "王国崛起：酒馆传说"),
        _gp_record("com.gamespark.topking.gp", "顶级领主"),
    ])
    assert r1["baselined"] == 2 and r1["new_apps"] == 0

    r2 = await ingest_artist_apps(account["id"], [
        {"wrapperType": "software", "trackId": "com.gamespark.mykingdom.gp", "_seen_storefronts": {"gp"}},
        {"wrapperType": "software", "trackId": "com.gamespark.topking.gp", "_seen_storefronts": {"gp"}},
        _gp_record("com.gamespark.crownrise.gp", "王冠崛起"),  # GP 无 release_date → 按新处理
    ])
    assert r2["baselined"] == 0 and r2["new_apps"] == 1

    resp = await client.get("/api/newcomers/appstore")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    it = items[0]
    assert it["platform"] == "gp"
    assert it["track_id"] == "com.gamespark.crownrise.gp"
    assert it["name"] == "王冠崛起"
    assert it["storefronts"] == ["gp"]


@pytest.mark.asyncio
async def test_gp_sync_mock_mode_noop(client, monkeypatch):
    import importlib
    gp = importlib.import_module("app.services.gp_releases")
    monkeypatch.setattr(gp.settings, "USE_MOCK_DATA", True)
    summary = await gp.sync_gp_releases()
    assert summary == {"gp_synced": 0, "gp_failed": 0, "gp_baselined": 0, "gp_new_apps": 0}


@pytest.mark.asyncio
async def test_gp_sync_real_mode_with_failure_isolation(client, monkeypatch):
    """两个 GP 账号：一个开发者页拉取失败计 failed，另一个正常入基线。"""
    import importlib
    gp = importlib.import_module("app.services.gp_releases")

    entity, ok_account = await _mk_entity_with_gp_account(client)
    r = await client.post(f"/api/publishers/{entity['id']}/itunes-artists",
                          json={"artist_id": "坏掉的账号", "platform": "gp"})
    assert r.status_code == 201

    async def fake_fetch(dev_id, known):
        if dev_id == "坏掉的账号":
            raise RuntimeError("boom")
        return [_gp_record("com.gamespark.mykingdom.gp", "王国崛起：酒馆传说")]

    monkeypatch.setattr(gp.settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(gp, "fetch_gp_records", fake_fetch)
    monkeypatch.setattr(gp, "_POLITE_DELAY_S", 0)
    summary = await gp.sync_gp_releases()
    assert summary["gp_synced"] == 1
    assert summary["gp_failed"] == 1
    assert summary["gp_baselined"] == 1
    assert summary["gp_new_apps"] == 0


@pytest.mark.asyncio
async def test_ios_sync_skips_gp_accounts(client, monkeypatch):
    """iOS 侧 sync 只取 platform='ios' 账号——GP 账号丢给 iTunes lookup 会 400。"""
    import importlib
    it = importlib.import_module("app.services.itunes_releases")

    await _mk_entity_with_gp_account(client)  # 只有一个 GP 账号

    called = []

    async def fake_multi(artist_id):
        called.append(artist_id)
        return []

    monkeypatch.setattr(it.settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(it, "fetch_artist_apps_multi", fake_multi)
    summary = await it.sync_itunes_releases()
    assert called == []  # GP 账号没被 iTunes 侧碰
    assert summary["synced"] == 0 and summary["failed"] == 0
