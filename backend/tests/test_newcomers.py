"""新品监测：本地零配额「新面孔」检测。

核心验证：
- 过去 W 个快照没出现过、as_of 进 TopN = 新面孔
- 窗口语义：只看最近 W 个快照(更早出现过、但窗口外 → 仍算新面孔)
- **故意不走 is_slg**：非 SLG 发行商的新面孔照样进列表(与 movement 相反)
- TopN 门槛、缺历史快照(no_baseline)、最近快照锚定(as_of != today)、中文夹具
"""
import pytest

SLG_PUB = "Century Games Pte. Ltd."
NON_SLG_PUB = "Supercell"


async def _seed(date, rows, country="US", platform="ios"):
    """rows: list of (app_id, rank, revenue, publisher)。name 默认用 app_id。"""
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking
    async with AsyncSessionLocal() as db:
        for app_id, rank, revenue, pub in rows:
            db.add(GameRanking(
                app_id=app_id, date=date, rank=rank, downloads=None,
                revenue=revenue, country=country, platform=platform,
                name=app_id, publisher=pub, icon_url=None,
            ))
        await db.commit()


@pytest.mark.asyncio
async def test_newcomer_first_appearance(client):
    """as_of 进 Top、过去快照从没见过 → 新面孔；老熟人不算。"""
    from app.services.newcomers import detect_newcomers
    await _seed("2026-05-01", [("veteran", 1, None, SLG_PUB)])
    await _seed("2026-05-08", [("veteran", 1, None, SLG_PUB)])
    await _seed("2026-05-15", [("veteran", 1, None, SLG_PUB), ("rookie", 4, None, SLG_PUB)])

    s = await detect_newcomers("US", "ios", window=4, topn=50)
    assert s["as_of"] == "2026-05-15"
    assert [n["name"] for n in s["newcomers"]] == ["rookie"]
    assert s["newcomers"][0]["rank"] == 4


@pytest.mark.asyncio
async def test_climber_not_newcomer(client):
    """过去在榜(哪怕名次靠后)、本期升进 TopN 的老产品 → 不是新面孔。"""
    from app.services.newcomers import detect_newcomers
    # rookie 上期就在 80 名(全榜基线能看到)，本期升到 5 → 是窜升不是新面孔
    await _seed("2026-05-08", [("a", 1, None, SLG_PUB), ("rookie", 80, None, SLG_PUB)])
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB), ("rookie", 5, None, SLG_PUB)])

    s = await detect_newcomers("US", "ios", window=4, topn=50)
    assert s["newcomers"] == []


@pytest.mark.asyncio
async def test_window_only_looks_back_w_snapshots(client):
    """只回看最近 W 个快照：更早出现过、但已滑出窗口 → 仍算新面孔(窗口记忆)。"""
    from app.services.newcomers import detect_newcomers
    # 'ghost' 只在最旧那期出现过；window=4 时基线取最近 4 个历史快照(不含最旧)
    await _seed("2026-05-01", [("a", 1, None, SLG_PUB), ("ghost", 9, None, SLG_PUB)])
    await _seed("2026-05-08", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-22", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-29", [("a", 1, None, SLG_PUB)])
    await _seed("2026-06-05", [("a", 1, None, SLG_PUB), ("ghost", 7, None, SLG_PUB)])

    s = await detect_newcomers("US", "ios", window=4, topn=50)
    # 基线只覆盖 05-08~05-29 四期，不含 05-01 → ghost 重新算新面孔
    assert [n["name"] for n in s["newcomers"]] == ["ghost"]


@pytest.mark.asyncio
async def test_non_slg_newcomer_included(client):
    """关键差异：新品监测**不走 is_slg**——非 SLG 发行商的新面孔照样上榜，
    并打 is_slg=False 标记供前端区分(这正是 movement 会过滤掉、却最该看的新厂商)。"""
    from app.services.newcomers import detect_newcomers
    await _seed("2026-05-08", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-15", [
        ("a", 1, None, SLG_PUB),
        ("slg_new", 3, None, SLG_PUB),
        ("unknown_new", 6, None, NON_SLG_PUB),
    ])

    s = await detect_newcomers("US", "ios", window=4, topn=50)
    names = {n["name"] for n in s["newcomers"]}
    assert names == {"slg_new", "unknown_new"}, "非 SLG 发行商的新面孔不该被过滤"
    flags = {n["name"]: n["is_slg"] for n in s["newcomers"]}
    assert flags["slg_new"] is True and flags["unknown_new"] is False


@pytest.mark.asyncio
async def test_ignored_publisher_and_appid_excluded(client):
    """缺口忽略名单(`publisher_ignores`)里人工确认的非 SLG 噪声(误挂 strategy 标签的
    宝可梦对战/塔防等)被剔除；**不在名单**里的非 SLG 新厂(未识别的真线索)仍照常浮现。
    口径与 /gaps 一致：publisher 走 corp_squash 归一键，app_id 精确剔。"""
    from app.services.newcomers import detect_newcomers
    from app.database import AsyncSessionLocal
    from app.models.publisher import PublisherIgnore
    from app.services.name_match import corp_squash
    from app.services.slg_publishers import _tokens

    await _seed("2026-05-08", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-15", [
        ("a", 1, None, SLG_PUB),
        ("noise_pub", 4, None, "The Pokemon Company"),  # 发行商粒度忽略
        ("noise_app", 5, None, "Random Studio Inc."),   # 单 app 粒度忽略
        ("real_lead", 6, None, "Brand New SLG Co."),     # 不在名单 → 保留(真线索)
    ])
    async with AsyncSessionLocal() as db:
        db.add(PublisherIgnore(kind="publisher",
                               value=corp_squash(_tokens("The Pokemon Company")),
                               label="The Pokemon Company"))
        db.add(PublisherIgnore(kind="app_id", value="noise_app", label="noise app"))
        await db.commit()

    s = await detect_newcomers("US", "ios", window=4, topn=50)
    names = {n["name"] for n in s["newcomers"]}
    assert names == {"real_lead"}, "忽略名单覆盖的应剔除、未识别真新厂应保留"


@pytest.mark.asyncio
async def test_topn_threshold(client):
    """名次 > TopN 的新进 app 不算"新进榜"(榜尾噪声不提示)。"""
    from app.services.newcomers import detect_newcomers
    await _seed("2026-05-08", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-15", [
        ("a", 1, None, SLG_PUB),
        ("in_top", 10, None, SLG_PUB),
        ("too_low", 25, None, SLG_PUB),
    ])

    s = await detect_newcomers("US", "ios", window=4, topn=20)
    assert [n["name"] for n in s["newcomers"]] == ["in_top"]


@pytest.mark.asyncio
async def test_no_baseline_when_cold(client):
    """只有一个快照(无历史可比) → no_baseline=True、空列表，绝不把首图全员当新品。"""
    from app.services.newcomers import detect_newcomers
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB), ("b", 2, None, SLG_PUB)])

    s = await detect_newcomers("US", "ios", window=4, topn=50)
    assert s["as_of"] == "2026-05-15"
    assert s["no_baseline"] is True
    assert s["newcomers"] == []


@pytest.mark.asyncio
async def test_no_data_returns_empty(client):
    """该 combo 库内完全无数据 → as_of=None、空列表，不抛错。"""
    from app.services.newcomers import detect_newcomers
    s = await detect_newcomers("KR", "android", window=4, topn=50)
    assert s["as_of"] is None
    assert s["no_baseline"] is False
    assert s["newcomers"] == []


@pytest.mark.asyncio
async def test_newcomer_distinguishes_true_first_from_reentry(client):
    """检测层区分「真首发」(从未见过) vs 「回归」(老游戏跌出 baseline 又回来)——
    通过 is_reentry 字段透传给消费方（digest 据此过滤、前端可加 tag 展示）。

    实景：weekly combo 老 SLG 产品（"old_reentry"）在更早快照里出现过、最近 W 周
    跌出基线、本期回来。和「真首发」（"true_new"）从未出现过的同期 newcomer 一起返回，
    分别打 is_reentry=True/False。
    """
    from app.services.newcomers import detect_newcomers
    # baseline 窗口之外的更早快照里出现过：算回归
    await _seed("2026-04-01", [("a", 1, None, SLG_PUB), ("old_reentry", 5, None, SLG_PUB)])
    # baseline 窗口（4 个快照）
    await _seed("2026-05-08", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-22", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-29", [("a", 1, None, SLG_PUB)])
    # as_of：两个 newcomer 同时进榜
    await _seed("2026-06-05", [
        ("a", 1, None, SLG_PUB),
        ("old_reentry", 4, None, SLG_PUB),  # 04-01 见过，算回归
        ("true_new", 6, None, SLG_PUB),     # 全历史从未见过，算真首发
    ])

    s = await detect_newcomers("US", "ios", window=4, topn=50)
    flags = {n["name"]: n["is_reentry"] for n in s["newcomers"]}
    assert flags == {"old_reentry": True, "true_new": False}


@pytest.mark.asyncio
async def test_no_baseline_combo_has_no_is_reentry_field(client):
    """no_baseline 路径（早期 return）不会附加 is_reentry——保留缺省语义，
    消费方按 `n.get('is_reentry')` 取值时拿到 None（falsy = 当真首发处理，
    避免冷库 combo 上来就被 digest 全过滤）。"""
    from app.services.newcomers import detect_newcomers
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB), ("b", 2, None, SLG_PUB)])
    s = await detect_newcomers("US", "ios", window=4, topn=50)
    assert s["no_baseline"] is True
    assert s["newcomers"] == []  # no_baseline 还是空，行为不变


@pytest.mark.asyncio
async def test_anchors_latest_snapshot_not_today(client):
    """as_of 取最近一次已同步快照(可早于今天)，页面始终有内容。"""
    from app.services.newcomers import detect_newcomers
    await _seed("2026-05-08", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB), ("rookie", 2, None, SLG_PUB)])

    s = await detect_newcomers("US", "ios", window=4, topn=50)
    assert s["as_of"] == "2026-05-15"  # 不是今天
    assert [n["name"] for n in s["newcomers"]] == ["rookie"]


@pytest.mark.asyncio
async def test_cjk_publisher_and_name(client):
    """中文游戏名 / 发行商夹具——确保字段透传无碍(CJK 验证)。"""
    from app.database import AsyncSessionLocal
    from app.models.game import GameRanking
    from app.services.newcomers import detect_newcomers
    async with AsyncSessionLocal() as db:
        db.add(GameRanking(app_id="old", date="2026-05-08", rank=1, country="US",
                           platform="ios", name="老牌强者", publisher="某老厂"))
        db.add(GameRanking(app_id="old", date="2026-05-15", rank=1, country="US",
                           platform="ios", name="老牌强者", publisher="某老厂"))
        db.add(GameRanking(app_id="newcjk", date="2026-05-15", rank=3, country="US",
                           platform="ios", name="末日寒冬：生存", publisher="江娱互动"))
        await db.commit()

    s = await detect_newcomers("US", "ios", window=4, topn=50)
    assert len(s["newcomers"]) == 1
    n = s["newcomers"][0]
    assert n["name"] == "末日寒冬：生存"
    assert n["publisher"] == "江娱互动"


@pytest.mark.asyncio
async def test_router_single_combo(client):
    """端点单 combo：返回扁平 items + as_of_by_combo + 口径回显，不发任何告警。"""
    await _seed("2026-05-08", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB), ("rookie", 4, None, NON_SLG_PUB)])

    r = await client.get("/api/newcomers/", params={"country": "US", "platform": "ios"})
    assert r.status_code == 200
    body = r.json()
    assert [i["name"] for i in body["items"]] == ["rookie"]
    assert body["items"][0]["is_slg"] is False
    assert body["as_of_by_combo"]["US/ios"] == "2026-05-15"
    assert body["window"] >= 1 and body["topn"] >= 1


@pytest.mark.asyncio
async def test_router_aggregates_across_combos(client, monkeypatch):
    """不传 country/platform → 跨 SYNC_RANKING_COMBOS 汇总，按名次升序。"""
    from app.config import settings
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios,JP:ios")

    await _seed("2026-05-08", [("ua", 1, None, SLG_PUB)], country="US", platform="ios")
    await _seed("2026-05-15", [("ua", 1, None, SLG_PUB), ("us_new", 8, None, SLG_PUB)],
                country="US", platform="ios")
    await _seed("2026-05-08", [("ja", 1, None, SLG_PUB)], country="JP", platform="ios")
    await _seed("2026-05-15", [("ja", 1, None, SLG_PUB), ("jp_new", 3, None, SLG_PUB)],
                country="JP", platform="ios")

    r = await client.get("/api/newcomers/")
    body = r.json()
    # 跨 combo 按名次升序：jp_new(#3) 在 us_new(#8) 前
    assert [i["name"] for i in body["items"]] == ["jp_new", "us_new"]


@pytest.mark.asyncio
async def test_router_reports_combos_without_baseline(client, monkeypatch):
    """缺历史快照的 combo 进 combos_without_baseline，不抛错。"""
    from app.config import settings
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios,JP:ios")
    # US/ios 有历史；JP/ios 只有一期(冷库)
    await _seed("2026-05-08", [("a", 1, None, SLG_PUB)], country="US", platform="ios")
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB)], country="US", platform="ios")
    await _seed("2026-05-15", [("j", 1, None, SLG_PUB)], country="JP", platform="ios")

    r = await client.get("/api/newcomers/")
    body = r.json()
    assert "JP/ios" in body["combos_without_baseline"]
    assert "US/ios" not in body["combos_without_baseline"]


# ── 厂商主体新品（P1：已建档主体 × 任意名次首次出现）─────────────────────────

async def _mk_entity(client, name, aliases=(), app_ids=()):
    r = await client.post("/api/publishers/", json={
        "name": name,
        "aliases": [{"keyword": k} for k in aliases],
        "app_ids": [{"app_id": a} for a in app_ids],
    })
    assert r.status_code == 201
    return r.json()


@pytest.mark.asyncio
async def test_publisher_newcomer_alias_match_any_rank(client):
    """已建档主体的新品在 TopN 之外首次出现 → 命中；无关发行商新品 → 不出现。"""
    from app.services.newcomers import detect_publisher_newcomers
    e = await _mk_entity(client, "江娱互动测试", aliases=["river game"])

    await _seed("2026-05-08", [("topwar", 5, None, "River Game HK Limited")])
    await _seed("2026-05-15", [
        ("topwar", 5, None, "River Game HK Limited"),          # 老产品：不报
        ("topheroes", 80, None, "River Game HK Limited"),      # 主体新品、80 名：报
        ("stranger", 81, None, "Some Unknown Studio"),         # 无关发行商：不报
    ])

    s = await detect_publisher_newcomers("US", "ios", window=4)
    assert [n["name"] for n in s["newcomers"]] == ["topheroes"]
    n = s["newcomers"][0]
    assert n["entity_id"] == e["id"]
    assert n["entity_name"] == "江娱互动测试"
    assert n["matched_by"] == "alias"
    assert n["rank"] == 80


@pytest.mark.asyncio
async def test_publisher_newcomer_respects_publisher_topn_default(client):
    """默认 PUBLISHER_NEWCOMER_TOPN=200 砍掉榜尾长尾。在 #200 内（含 #200）的命中，
    #201+ 即使首次出现也不报——治 JP/android weekly 抖动产生 #535 类长尾刷屏 digest。"""
    from app.services.newcomers import detect_publisher_newcomers
    await _mk_entity(client, "TopN 测试", aliases=["river game"])
    await _seed("2026-05-08", [("anchor", 1, None, "River Game HK Limited")])
    await _seed("2026-05-15", [
        ("anchor", 1, None, "River Game HK Limited"),
        ("on_edge", 200, None, "River Game HK Limited"),  # 恰好 #200，进
        ("too_deep", 201, None, "River Game HK Limited"),  # #201 排除
    ])
    s = await detect_publisher_newcomers("US", "ios", window=4)
    names = [n["name"] for n in s["newcomers"]]
    assert "on_edge" in names
    assert "too_deep" not in names


@pytest.mark.asyncio
async def test_publisher_newcomer_respects_explicit_topn_override(client):
    """显式传 topn 覆盖默认值——API 调用方可放宽收紧门槛。"""
    from app.services.newcomers import detect_publisher_newcomers
    await _mk_entity(client, "覆盖测试", aliases=["river game"])
    await _seed("2026-05-08", [("anchor", 1, None, "River Game HK Limited")])
    await _seed("2026-05-15", [
        ("anchor", 1, None, "River Game HK Limited"),
        ("low_rank", 300, None, "River Game HK Limited"),
    ])
    # 默认 topn=200，#300 不报
    s_default = await detect_publisher_newcomers("US", "ios", window=4)
    assert [n["name"] for n in s_default["newcomers"]] == []
    # 显式放宽到 500，#300 报
    s_loose = await detect_publisher_newcomers("US", "ios", window=4, topn=500)
    assert [n["name"] for n in s_loose["newcomers"]] == ["low_rank"]


@pytest.mark.asyncio
async def test_publisher_newcomer_is_reentry_field(client):
    """detect_publisher_newcomers 也透传 is_reentry——digest 用它过滤回归。"""
    from app.services.newcomers import detect_publisher_newcomers
    await _mk_entity(client, "回归测试", aliases=["river game"])
    # baseline 之外的更早快照
    await _seed("2026-04-01", [("old_back", 50, None, "River Game HK Limited")])
    # baseline 4 个快照内不见
    await _seed("2026-05-08", [("anchor", 1, None, "River Game HK Limited")])
    await _seed("2026-05-15", [("anchor", 1, None, "River Game HK Limited")])
    await _seed("2026-05-22", [("anchor", 1, None, "River Game HK Limited")])
    await _seed("2026-05-29", [("anchor", 1, None, "River Game HK Limited")])
    # as_of：回归 + 真首发同时上
    await _seed("2026-06-05", [
        ("anchor", 1, None, "River Game HK Limited"),
        ("old_back", 60, None, "River Game HK Limited"),
        ("true_new", 100, None, "River Game HK Limited"),
    ])
    s = await detect_publisher_newcomers("US", "ios", window=4)
    flags = {n["name"]: n["is_reentry"] for n in s["newcomers"]}
    assert flags == {"old_back": True, "true_new": False}


@pytest.mark.asyncio
async def test_publisher_newcomer_app_id_pin(client):
    """钉选 app_id 命中（发行商名对不上也能归属）。"""
    from app.services.newcomers import detect_publisher_newcomers
    e = await _mk_entity(client, "钉选主体", app_ids=["com.pin.newgame"])

    await _seed("2026-05-08", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-15", [
        ("a", 1, None, SLG_PUB),
        ("com.pin.newgame", 120, None, "马甲未知发行商"),
    ])

    s = await detect_publisher_newcomers("US", "ios", window=4)
    assert len(s["newcomers"]) == 1
    assert s["newcomers"][0]["entity_id"] == e["id"]
    assert s["newcomers"][0]["matched_by"] == "app_id"


@pytest.mark.asyncio
async def test_publisher_newcomers_endpoint(client, monkeypatch):
    """端点跨 combo 汇总 + no_baseline 标记 + 中文主体名。"""
    from app.config import settings
    monkeypatch.setattr(settings, "SYNC_RANKING_COMBOS", "US:ios,JP:ios")
    await _mk_entity(client, "壳木游戏测试", aliases=["camel games"])

    await _seed("2026-05-08", [("old", 1, None, SLG_PUB)], country="US", platform="ios")
    await _seed("2026-05-15", [
        ("old", 1, None, SLG_PUB),
        ("ageofx", 95, None, "Camel Games Limited"),
    ], country="US", platform="ios")
    await _seed("2026-05-15", [("jp_only", 1, None, SLG_PUB)], country="JP", platform="ios")  # 冷库

    r = await client.get("/api/newcomers/publishers")
    assert r.status_code == 200
    body = r.json()
    assert [i["name"] for i in body["items"]] == ["ageofx"]
    assert body["items"][0]["entity_name"] == "壳木游戏测试"
    assert body["items"][0]["country"] == "US"
    assert "JP/ios" in body["combos_without_baseline"]
    assert body["window"] >= 1
