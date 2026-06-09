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
