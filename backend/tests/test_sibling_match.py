"""跨平台同款游戏识别（find_sibling_app_ids）+ /coverage、/metrics 的
`merge_siblings=true` 集成行为。

规则与前端 lib/aggregateMerge 严格对齐：同 publisher 规范化等同、名字一方是
另一方规范化前缀且短的 ≥ 5。名字归一时优先取 US 行。
"""
import pytest
from datetime import date, timedelta


async def _seed(rows):
    """rows: (app_id, date, rank, downloads, revenue, country, platform, name, publisher)。"""
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking
    async with AsyncSessionLocal() as db:
        for aid, d, rk, dl, rv, c, p, name, pub in rows:
            db.add(GameRanking(app_id=aid, date=d, rank=rk, downloads=dl,
                               revenue=rv, country=c, platform=p,
                               name=name, publisher=pub))
        await db.commit()


@pytest.mark.asyncio
async def test_normalize_ident_strips_case_and_punctuation():
    from app.services.sibling_match import normalize_ident
    assert normalize_ident("Century Games Pte. Ltd.") == "centurygamespteltd"
    assert normalize_ident("Century Games PTE. LTD.") == "centurygamespteltd"
    assert normalize_ident("Last War:Survival Game") == "lastwarsurvivalgame"
    assert normalize_ident(None) == ""
    assert normalize_ident("") == ""


@pytest.mark.asyncio
async def test_find_siblings_groups_ios_android_with_case_diff_publisher(client):
    """publisher 大小写 / 标点不同但规范化等同 + 名字一致 → 同款。"""
    from app.services.sibling_match import find_sibling_app_ids
    from app.database import AsyncSessionLocal

    today = date.today().strftime("%Y-%m-%d")
    await _seed([
        ("ios.kingshot", today, 1, 100, 10.0, "US", "ios",
         "Kingshot", "Century Games Pte. Ltd."),
        ("and.kingshot", today, 1, 80, 8.0, "US", "android",
         "Kingshot", "Century Games PTE. LTD."),
    ])
    async with AsyncSessionLocal() as db:
        siblings = await find_sibling_app_ids(db, "ios.kingshot")
        assert set(siblings) == {"ios.kingshot", "and.kingshot"}


@pytest.mark.asyncio
async def test_find_siblings_prefix_match_for_name_with_suffix(client):
    """'Last War:Survival' 与 'Last War:Survival Game' 短规范化 ≥5，
    一方是另一方前缀 → 同款。"""
    from app.services.sibling_match import find_sibling_app_ids
    from app.database import AsyncSessionLocal

    today = date.today().strftime("%Y-%m-%d")
    await _seed([
        ("ios.lw", today, 1, 100, 10.0, "US", "ios",
         "Last War:Survival", "FUNFLY PTE. LTD."),
        ("and.lw", today, 1, 80, 8.0, "US", "android",
         "Last War:Survival Game", "FUNFLY PTE. LTD."),
    ])
    async with AsyncSessionLocal() as db:
        s = await find_sibling_app_ids(db, "and.lw")
        assert set(s) == {"ios.lw", "and.lw"}


@pytest.mark.asyncio
async def test_find_siblings_rejects_short_prefix(client):
    """短的规范化 < 5 字符 → 拒合（防 'Z' 被吞进 'ZGame'）。"""
    from app.services.sibling_match import find_sibling_app_ids
    from app.database import AsyncSessionLocal

    today = date.today().strftime("%Y-%m-%d")
    await _seed([
        ("a", today, 1, 1, 1.0, "US", "ios", "Z",     "Same Pub"),
        ("b", today, 1, 1, 1.0, "US", "android", "ZGame", "Same Pub"),
    ])
    async with AsyncSessionLocal() as db:
        s = await find_sibling_app_ids(db, "a")
        assert s == ["a"]


@pytest.mark.asyncio
async def test_find_siblings_does_not_cross_publisher(client):
    """跨 publisher 即使名字相同也不合（宁错放过别错合）。两个 publisher 都没建档为
    任何 entity 的 alias，规范化后字符串不等同 → 应判定为不同 publisher 不合并。"""
    from app.services.sibling_match import find_sibling_app_ids
    from app.database import AsyncSessionLocal

    today = date.today().strftime("%Y-%m-%d")
    await _seed([
        ("a", today, 1, 1, 1.0, "US", "ios", "Kingdom", "Pub A"),
        ("b", today, 1, 1, 1.0, "US", "android", "Kingdom", "Pub B"),
    ])
    async with AsyncSessionLocal() as db:
        s = await find_sibling_app_ids(db, "a")
        assert s == ["a"]


@pytest.mark.asyncio
async def test_find_siblings_merges_across_alias_canonicalized_publisher(client):
    """**核心新行为**：两个 publisher 字符串都通过 `publisher_aliases` 映射到同一个 entity
    时，视为同 publisher → 跨平台同款合并。

    实景：Top Games 同时用 \"TOP GAMES INC.\"（iOS）和 \"TG Inc.\"（Android）发 Evony。
    规范化字符串 \"topgamesinc\" vs \"tginc\" 完全不等，但建档时两个 alias 都已挂到
    entity 15 → canonical key 用 \"@e:15\" 让它们等价。
    """
    from app.services.sibling_match import find_sibling_app_ids
    from app.database import AsyncSessionLocal

    today = date.today().strftime("%Y-%m-%d")
    # Top Games 主体 + 两个 alias（覆盖两种 publisher 写法）
    await client.post("/api/publishers/", json={
        "name": "Top Games",
        "aliases": [{"keyword": "top games"}, {"keyword": "tg inc"}],
    })
    await _seed([
        ("ios.evony", today, 1, 200, 100.0, "US", "ios",
         "Evony", "TOP GAMES INC."),
        ("gp.evony", today, 2, 150, 80.0, "US", "android",
         "Evony: The King's Return", "TG Inc."),
    ])
    async with AsyncSessionLocal() as db:
        s = await find_sibling_app_ids(db, "ios.evony")
        assert set(s) == {"ios.evony", "gp.evony"}
        # 反向也对称（站在 Android 也能找回 iOS）
        s2 = await find_sibling_app_ids(db, "gp.evony")
        assert set(s2) == {"ios.evony", "gp.evony"}


@pytest.mark.asyncio
async def test_find_siblings_does_not_cross_different_entities(client):
    """**反向回归**：两个 publisher 字符串映射到**不同** entity 时即使名字前缀匹配也不合
    （宁错放过别错合）。如「Whiteout Survival」由 entity 1 发 iOS、entity 2 发 Android。"""
    from app.services.sibling_match import find_sibling_app_ids
    from app.database import AsyncSessionLocal

    today = date.today().strftime("%Y-%m-%d")
    await client.post("/api/publishers/", json={
        "name": "Studio A", "aliases": [{"keyword": "studio a"}],
    })
    await client.post("/api/publishers/", json={
        "name": "Studio B", "aliases": [{"keyword": "studio b"}],
    })
    await _seed([
        ("ios.same", today, 1, 100, 10.0, "US", "ios",
         "Whiteout Survival", "Studio A Limited"),
        ("gp.same", today, 1, 80, 8.0, "US", "android",
         "Whiteout Survival", "Studio B Inc."),
    ])
    async with AsyncSessionLocal() as db:
        s = await find_sibling_app_ids(db, "ios.same")
        # 同名但跨 entity → 不合
        assert s == ["ios.same"]


@pytest.mark.asyncio
async def test_find_siblings_prefers_us_name_over_localized(client):
    """target 的 US 行用英文名，姐妹 app 也只在 KR/JP 有非 US 行时仍能匹配——
    canonical 名字按 US 优先取，KR/JP 的本地化名（킹샷/ホワイト...）只当 fallback。"""
    from app.services.sibling_match import find_sibling_app_ids
    from app.database import AsyncSessionLocal

    today = date.today().strftime("%Y-%m-%d")
    await _seed([
        # ios.kingshot: 既有 US 英文行也有 KR 本地化行
        ("ios.kingshot", today, 1, 100, 10.0, "US", "ios",
         "Kingshot", "Century Games Pte. Ltd."),
        ("ios.kingshot", today, 2, 50, 5.0, "KR", "ios",
         "킹샷:Kingshot", "Century Games Pte. Ltd."),
        # and.kingshot: 只有 US 英文
        ("and.kingshot", today, 1, 80, 8.0, "US", "android",
         "Kingshot", "Century Games PTE. LTD."),
    ])
    async with AsyncSessionLocal() as db:
        s = await find_sibling_app_ids(db, "ios.kingshot")
        # US 名 "Kingshot" 被优先取作 canonical，与 and.kingshot 的 "Kingshot" 等同
        assert set(s) == {"ios.kingshot", "and.kingshot"}


@pytest.mark.asyncio
async def test_find_siblings_returns_self_when_target_absent(client):
    """target app_id 没在 game_rankings 出现 → 没法找姐妹，原样返回 [self]。"""
    from app.services.sibling_match import find_sibling_app_ids
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        assert await find_sibling_app_ids(db, "ghost.app") == ["ghost.app"]


@pytest.mark.asyncio
async def test_coverage_merge_siblings_unions_markets_across_siblings(client):
    """/coverage?merge_siblings=true 返回姐妹 app_id 全部 (国家,平台) 的并集。"""
    today = date.today().strftime("%Y-%m-%d")
    await _seed([
        ("ios.kingshot", today, 1, 100, 10.0, "US", "ios",
         "Kingshot", "Century Games Pte. Ltd."),
        ("ios.kingshot", today, 2, 80, 8.0, "JP", "ios",
         "Kingshot", "Century Games Pte. Ltd."),
        ("and.kingshot", today, 1, 60, 6.0, "US", "android",
         "Kingshot", "Century Games PTE. LTD."),
    ])
    # 不开 merge_siblings：仅 ios.kingshot 自身 2 个市场
    r1 = await client.get("/api/games/ios.kingshot/coverage")
    assert {(c["country"], c["platform"]) for c in r1.json()} == {("US", "ios"), ("JP", "ios")}

    # 开 merge_siblings：含 and.kingshot 的 US/android
    r2 = await client.get("/api/games/ios.kingshot/coverage", params={"merge_siblings": "true"})
    assert {(c["country"], c["platform"]) for c in r2.json()} == {
        ("US", "ios"), ("JP", "ios"), ("US", "android"),
    }


@pytest.mark.asyncio
async def test_metrics_aggregate_merge_siblings_sums_across_app_ids(client):
    """/metrics?aggregate=true&merge_siblings=true 跨姐妹 app_id 一并按日合计。"""
    today = date.today().strftime("%Y-%m-%d")
    await _seed([
        ("ios.lw", today, 1, 100, 10.0, "US", "ios",
         "Last War:Survival", "FUNFLY PTE. LTD."),
        ("and.lw", today, 1, 80, 8.0, "US", "android",
         "Last War:Survival Game", "FUNFLY PTE. LTD."),
    ])
    # 不开：只算 ios.lw
    r1 = await client.get("/api/games/ios.lw/metrics",
                          params={"aggregate": "true", "days": 7})
    body1 = r1.json()
    assert sum(p["value"] for p in body1["revenue"]) == 10.0

    # 开 merge_siblings：合并 iOS+Android
    r2 = await client.get("/api/games/ios.lw/metrics",
                          params={"aggregate": "true", "merge_siblings": "true", "days": 7})
    body2 = r2.json()
    assert sum(p["value"] for p in body2["revenue"]) == 18.0  # 10 + 8


@pytest.mark.asyncio
async def test_metrics_single_market_merge_siblings_pulls_from_sibling(client, monkeypatch):
    """用户停在 Android app_id URL 但点 US·iOS chip 时，merge_siblings 让后端能
    从姐妹 iOS app_id 取出 US/iOS 数据（否则该组合查空 → ST 回退或为空）。

    测试环境 USE_MOCK_DATA=true，需 monkeypatch 才走真实 DB 分支（mock 模式会
    直接返回 ST 随机 mock，不会查 game_rankings）。"""
    from app.routers import games as games_router
    monkeypatch.setattr(games_router.sensor_tower_service, "use_mock", False)

    today = date.today().strftime("%Y-%m-%d")
    await _seed([
        ("ios.lw", today, 1, 100, 10.0, "US", "ios",
         "Last War:Survival", "FUNFLY PTE. LTD."),
        ("and.lw", today, 1, 80, 8.0, "US", "android",
         "Last War:Survival Game", "FUNFLY PTE. LTD."),
    ])
    # 站在 Android app_id，问 US/iOS 数据
    r = await client.get(
        "/api/games/and.lw/metrics",
        params={"country": "US", "platform": "ios", "days": 7, "merge_siblings": "true"},
    )
    body = r.json()
    # iOS 收入 10.0 来自姐妹 ios.lw
    assert sum(p["value"] for p in body["revenue"]) == 10.0
    assert sum(p["value"] for p in body["downloads"]) == 100
