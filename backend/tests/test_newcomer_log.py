"""新面孔检出沉淀（market_newcomer_log，新品监测 v2）。

核心验证：
- record：检出落库 + 富化字段写入；同 combo×app 唯一，重跑不重写（首报定格）
- /api/newcomers/history：时间窗 / 市场 / 平台 / topn 筛选；screenshots 解 JSON
- 富化失败留 NULL 不丢检出；中文夹具（CJK 纪律）
"""
import importlib
import json
from datetime import timedelta

import pytest


def _live(mod):
    """conftest 每 test 清 sys.modules——顶层 import 会拿到指向旧 engine 的过期模块，
    必须用 importlib 取活模块（见 project_shipped_history 持久 gotcha）。"""
    return importlib.import_module(mod)


async def _seed_rankings(today, prev, country, prefix):
    """country 与 app_id 前缀按用例隔离——测试库跨文件共享，复用夹具会撞唯一约束。"""
    database = _live("app.database")
    GameRanking = _live("app.models.game").GameRanking
    async with database.AsyncSessionLocal() as db:
        rows = [
            (f"{prefix}veteran", prev, 1), (f"{prefix}veteran", today, 1),
            (f"{prefix}rookie01", today, 4),   # 新面孔（Top50 内）
            (f"{prefix}rookie99", today, 88),  # 新面孔（50<rank<=100，历史口径收、Top50 筛选排除）
        ]
        for app_id, date, rank in rows:
            db.add(GameRanking(app_id=app_id, date=date, rank=rank, downloads=None,
                               revenue=12345.0 if app_id.endswith("rookie01") else None,
                               country=country, platform="ios",
                               name=f"测试游戏{app_id}", publisher="神秘工作室", icon_url=None))
        await db.commit()


@pytest.mark.asyncio
async def test_record_and_history_endpoint(client, monkeypatch):
    nl = importlib.import_module("app.services.newcomer_log")
    now = _live("app.database").utcnow_naive()
    today = now.strftime("%Y-%m-%d")
    prev = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    await _seed_rankings(today, prev, "DE", "a_")

    async def fake_enrich(app_id, country, platform):
        if app_id == "a_rookie01":
            return {"store_url": "https://apps.apple.com/us/app/id1", "genre": "Strategy",
                    "rating": 4.5, "rating_count": 100, "price": "Free",
                    "description": "中文描述：史诗策略大作。",
                    "screenshot_urls": json.dumps(["https://x/1.jpg"]),
                    "release_date": "2026-06-01", "enrich_source": "itunes"}
        return None  # rookie99 富化失败 → 留 NULL 不丢检出

    monkeypatch.setattr(nl.settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(nl, "enrich_fields", fake_enrich)
    monkeypatch.setattr(nl, "_POLITE_DELAY_S", 0)

    r1 = await nl.record_market_newcomers("DE", "ios")
    assert r1 == {"detected": 2, "recorded": 2, "enriched": 1}
    # 幂等：重跑不重写
    r2 = await nl.record_market_newcomers("DE", "ios")
    assert r2["recorded"] == 0

    resp = await client.get("/api/newcomers/history?days=7&country=DE")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    by_id = {i["app_id"]: i for i in items}
    rich = by_id["a_rookie01"]
    assert rich["genre"] == "Strategy" and rich["screenshots"] == ["https://x/1.jpg"]
    assert rich["name"] == "测试游戏a_rookie01" and rich["publisher"] == "神秘工作室"
    assert rich["country"] == "DE" and rich["platform"] == "ios"
    poor = by_id["a_rookie99"]
    assert poor["genre"] is None and poor["enrich_source"] is None  # 失败留 NULL

    # topn=50 筛掉 88 名的检出
    resp = await client.get("/api/newcomers/history?days=7&topn=50&country=DE")
    assert [i["app_id"] for i in resp.json()["items"]] == ["a_rookie01"]
    # 平台/市场筛选
    resp = await client.get("/api/newcomers/history?days=7&platform=android&country=DE")
    assert resp.json()["items"] == []
    resp = await client.get("/api/newcomers/history?days=7&country=JP")
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_record_includes_tracked_publisher_deep_climber(client, monkeypatch):
    """已建档主体的深位新品（100<rank<=200）：市场口径(Top100)漏，主体口径(Top200)
    接住并入库——专门补「冷启动名次深于 100、慢爬进榜时已被基线吞掉」的漏报。
    未建档的同名次深位仍被漏（市场口径之外不收），两路口径对称。"""
    nl = importlib.import_module("app.services.newcomer_log")
    database = _live("app.database")
    GameRanking = _live("app.models.game").GameRanking
    now = database.utcnow_naive()
    today = now.strftime("%Y-%m-%d")
    prev = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    prev2 = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    prev3 = (now - timedelta(days=3)).strftime("%Y-%m-%d")
    async with database.AsyncSessionLocal() as db:
        # baseline 锚点（≥3 个历史快照 + 今日都在 = 老面孔，不算新；满足
        # PUBLISHER_NEWCOMER_MIN_BASELINE 门控）
        for d in (prev3, prev2, prev, today):
            db.add(GameRanking(app_id="d_anchor", date=d, rank=1, country="AT",
                               platform="ios", name="锚", publisher="某厂"))
        # 已建档主体的深位首发（rank 144 > 100、<= 200）
        db.add(GameRanking(app_id="d_tracked", date=today, rank=144, country="AT",
                           platform="ios", name="深位慢爬新品", publisher="Tracked Studio"))
        # 未建档的深位首发（同名次档）——应继续被漏
        db.add(GameRanking(app_id="d_untracked", date=today, rank=150, country="AT",
                           platform="ios", name="未建档深位", publisher="Random Studio"))
        await db.commit()

    # 建档：钉住 d_tracked 的 app_id（detect_publisher_newcomers 走 app_id 归属）
    r = await client.post("/api/publishers/", json={
        "name": "深位测试主体", "app_ids": [{"app_id": "d_tracked"}]})
    assert r.status_code == 201

    monkeypatch.setattr(nl.settings, "USE_MOCK_DATA", True)  # 跳富化
    monkeypatch.setattr(nl, "_POLITE_DELAY_S", 0)
    out = await nl.record_market_newcomers("AT", "ios")
    assert out["recorded"] == 1  # 仅 d_tracked

    by_id = {i["app_id"]: i for i in
             (await client.get("/api/newcomers/history?days=7&country=AT")).json()["items"]}
    assert "d_tracked" in by_id, "已建档主体深位新品应入库"
    assert by_id["d_tracked"]["rank"] == 144
    assert "d_untracked" not in by_id, "未建档深位新品仍被漏（口径未变）"


@pytest.mark.asyncio
async def test_record_mock_mode_skips_enrich(client, monkeypatch):
    nl = importlib.import_module("app.services.newcomer_log")
    now = _live("app.database").utcnow_naive()
    today = now.strftime("%Y-%m-%d")
    prev = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    await _seed_rankings(today, prev, "FR", "b_")

    called = []
    async def fake_enrich(*a):
        called.append(a)
    monkeypatch.setattr(nl.settings, "USE_MOCK_DATA", True)
    monkeypatch.setattr(nl, "enrich_fields", fake_enrich)
    monkeypatch.setattr(nl, "_POLITE_DELAY_S", 0)
    r = await nl.record_market_newcomers("FR", "ios")
    assert r["recorded"] == 2 and r["enriched"] == 0 and called == []


@pytest.mark.asyncio
async def test_record_persists_is_reentry_and_history_filters_signal(client, monkeypatch):
    """检出落库时固化 is_reentry（真首发 vs 回归），/history 用 signal 参数筛选。

    场景：5 个 baseline 之外的更早快照里 `re_old` 出现过、4 个 baseline 快照里它不见。
    今天 `re_old` 回来 + `truly_new` 真首发同时进榜——录入后 is_reentry 字段固化。
    """
    nl = importlib.import_module("app.services.newcomer_log")
    database = _live("app.database")
    GameRanking = _live("app.models.game").GameRanking
    now = database.utcnow_naive()
    today = now.strftime("%Y-%m-%d")
    async with database.AsyncSessionLocal() as db:
        # 更早快照（baseline 之外）：re_old 出现过
        db.add(GameRanking(app_id="re_old", date="2026-04-01", rank=8,
                           country="NL", platform="ios", name="老回归", publisher="某厂"))
        db.add(GameRanking(app_id="anchor", date="2026-04-01", rank=1,
                           country="NL", platform="ios", name="锚", publisher="某厂"))
        # baseline 4 个快照：re_old 不见
        for d in ("2026-05-01", "2026-05-08", "2026-05-15", "2026-05-22"):
            db.add(GameRanking(app_id="anchor", date=d, rank=1,
                               country="NL", platform="ios", name="锚", publisher="某厂"))
        # 今天：re_old 回归 + truly_new 真首发
        db.add(GameRanking(app_id="anchor", date=today, rank=1,
                           country="NL", platform="ios", name="锚", publisher="某厂"))
        db.add(GameRanking(app_id="re_old", date=today, rank=7,
                           country="NL", platform="ios", name="老回归", publisher="某厂"))
        db.add(GameRanking(app_id="truly_new", date=today, rank=9,
                           country="NL", platform="ios", name="真首发", publisher="某厂"))
        await db.commit()

    monkeypatch.setattr(nl.settings, "USE_MOCK_DATA", True)  # 跳富化
    monkeypatch.setattr(nl, "_POLITE_DELAY_S", 0)
    r = await nl.record_market_newcomers("NL", "ios")
    assert r["recorded"] == 2  # re_old + truly_new 都首报

    # 默认 signal=all（不筛），两条都在
    all_items = (await client.get("/api/newcomers/history?days=120&country=NL")).json()["items"]
    by_id = {i["app_id"]: i for i in all_items}
    assert by_id["re_old"]["is_reentry"] is True
    assert by_id["truly_new"]["is_reentry"] is False

    # signal=true_new 仅真首发
    tn = (await client.get("/api/newcomers/history?days=120&country=NL&signal=true_new")).json()["items"]
    assert [i["app_id"] for i in tn] == ["truly_new"]

    # signal=reentry 仅回归
    rt = (await client.get("/api/newcomers/history?days=120&country=NL&signal=reentry")).json()["items"]
    assert [i["app_id"] for i in rt] == ["re_old"]


@pytest.mark.asyncio
async def test_history_signal_true_new_includes_legacy_null_rows(client):
    """0022 迁移前的历史行 is_reentry=NULL（无法回溯当时 baseline）——signal=true_new
    把 NULL 也算真首发（向后兼容，老卡片照旧显示）。"""
    database = _live("app.database")
    MarketNewcomerLog = _live("app.models.newcomer").MarketNewcomerLog
    async with database.AsyncSessionLocal() as db:
        # 直接灌一条 is_reentry=NULL 的「老数据」
        db.add(MarketNewcomerLog(
            country="PT", platform="ios", app_id="legacy_app", as_of="2026-05-01",
            name="老数据卡片", publisher="老厂", is_slg=True, is_reentry=None,
        ))
        await db.commit()

    tn = (await client.get("/api/newcomers/history?days=120&country=PT&signal=true_new")).json()["items"]
    assert [i["app_id"] for i in tn] == ["legacy_app"]
    assert tn[0]["is_reentry"] is None  # 字段透传，值是 NULL


@pytest.mark.asyncio
async def test_history_returns_as_of_by_combo_for_freshness(client):
    """as_of_by_combo 字段返回各 combo 最近一次已同步快照日，让前端给陈旧 combo
    加 stale 提示（如「JP/android 截至 14 天前」）。"""
    database = _live("app.database")
    GameRanking = _live("app.models.game").GameRanking
    async with database.AsyncSessionLocal() as db:
        db.add(GameRanking(app_id="x", date="2026-06-21", rank=1,
                           country="ES", platform="ios", name="x", publisher="x"))
        db.add(GameRanking(app_id="y", date="2026-06-07", rank=1,
                           country="ES", platform="android", name="y", publisher="y"))
        await db.commit()

    body = (await client.get("/api/newcomers/history?days=30")).json()
    assert body["as_of_by_combo"]["ES/ios"] == "2026-06-21"
    assert body["as_of_by_combo"]["ES/android"] == "2026-06-07"


@pytest.mark.asyncio
async def test_history_live_attribution(client, monkeypatch):
    """建档发生在检出之后 → 历史端点读时归属：entity_name 出现、is_slg 翻真。"""
    nl = importlib.import_module("app.services.newcomer_log")
    now = _live("app.database").utcnow_naive()
    today = now.strftime("%Y-%m-%d")
    prev = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    await _seed_rankings(today, prev, "GB", "c_")

    monkeypatch.setattr(nl.settings, "USE_MOCK_DATA", True)  # 跳过外呼
    monkeypatch.setattr(nl, "_POLITE_DELAY_S", 0)
    await nl.record_market_newcomers("GB", "ios")

    resp = await client.get("/api/newcomers/history?days=7&country=GB")
    assert all(not i["is_slg"] and i["entity_name"] is None for i in resp.json()["items"])

    # 事后建档：钉住 c_rookie01 的 app_id
    r = await client.post("/api/publishers/", json={
        "name": "新检出厂商甲", "app_ids": [{"app_id": "c_rookie01"}]})
    assert r.status_code == 201

    resp = await client.get("/api/newcomers/history?days=7&country=GB")
    by_id = {i["app_id"]: i for i in resp.json()["items"]}
    hit = by_id["c_rookie01"]
    assert hit["is_slg"] is True and hit["entity_name"] == "新检出厂商甲"
    assert by_id["c_rookie99"]["entity_name"] is None  # 未建档的不受影响


def test_digest_newcomer_enrich_suffix():
    from app.services.release_alerts import build_newcomer_lines
    market = {"newcomers": [{"app_id": "x1", "rank": 7, "name": "寒霜新游",
                             "publisher": "某厂", "revenue": None, "downloads": None, "is_slg": True}]}
    lines = build_newcomer_lines(market, {}, enrich={
        "x1": {"genre": "Casual", "price": "Free", "release_date": "2026-06-01"}})
    # 引用块子行（独立 \n\n 段，防钉钉续行粘连）：品类英译中（Casual→休闲）· 厂商归属
    # （未匹配主体退回发行商名）；price/上架日不展示
    assert lines == ["✨ **寒霜新游** 空降 **#7**\n\n> 休闲 · 厂商 某厂"]
    # 无富化数据时子行仅剩厂商（发行商名）一项
    lines2 = build_newcomer_lines(market, {})
    assert lines2 == ["✨ **寒霜新游** 空降 **#7**\n\n> 厂商 某厂"]


@pytest.mark.asyncio
async def test_prune_newcomer_log_drops_old_rows(client, monkeypatch):
    """prune 删除 first_detected_at 超过保留窗口的行，窗口内的保留；retention<=0 关闭。"""
    nl = importlib.import_module("app.services.newcomer_log")
    database = _live("app.database")
    MarketNewcomerLog = _live("app.models.newcomer").MarketNewcomerLog
    now = database.utcnow_naive()
    async with database.AsyncSessionLocal() as db:
        # 老行（400 天前）+ 新行（10 天前）
        old = MarketNewcomerLog(country="NO", platform="ios", app_id="old_app",
                                as_of="2025-01-01", name="超龄检出", publisher="老厂", is_slg=True)
        old.first_detected_at = now - timedelta(days=400)
        fresh = MarketNewcomerLog(country="NO", platform="ios", app_id="fresh_app",
                                  as_of="2026-06-01", name="新近检出", publisher="新厂", is_slg=True)
        fresh.first_detected_at = now - timedelta(days=10)
        db.add_all([old, fresh])
        await db.commit()

    # retention<=0 关闭：一行不删
    assert await nl.prune_newcomer_log(retention_days=0) == 0

    # 保留 365 天：老行删、新行留
    deleted = await nl.prune_newcomer_log(retention_days=365)
    assert deleted == 1
    items = (await client.get("/api/newcomers/history?days=365&country=NO")).json()["items"]
    assert {i["app_id"] for i in items} == {"fresh_app"}

    # 幂等：再跑无可删
    assert await nl.prune_newcomer_log(retention_days=365) == 0


@pytest.mark.asyncio
async def test_history_filters_ignored_publishers(client):
    """缺口忽略名单里的发行商，读时从 /history 过滤掉（行仍在表里、只是不返回），
    未忽略的真线索照常保留——口径与 /gaps、detect_newcomers 一致（corp_squash 键）。"""
    database = _live("app.database")
    MarketNewcomerLog = _live("app.models.newcomer").MarketNewcomerLog
    PublisherIgnore = _live("app.models.publisher").PublisherIgnore
    corp_squash = _live("app.services.name_match").corp_squash
    _tokens = _live("app.services.slg_publishers")._tokens

    async with database.AsyncSessionLocal() as db:
        db.add(MarketNewcomerLog(
            country="SE", platform="ios", app_id="noise_app", as_of="2026-05-01",
            name="宝可梦对战噪声", publisher="The Pokemon Company", is_slg=False, is_reentry=False,
        ))
        db.add(MarketNewcomerLog(
            country="SE", platform="ios", app_id="lead_app", as_of="2026-05-01",
            name="真 SLG 线索", publisher="Brand New SLG Co.", is_slg=False, is_reentry=False,
        ))
        db.add(PublisherIgnore(kind="publisher",
                               value=corp_squash(_tokens("The Pokemon Company")),
                               label="The Pokemon Company"))
        await db.commit()

    items = (await client.get("/api/newcomers/history?days=120&country=SE")).json()["items"]
    assert {i["app_id"] for i in items} == {"lead_app"}, \
        "被忽略的发行商应从 /history 过滤，未忽略线索保留"
