"""SLG 新品周察卡 build_weekly_newcomer_review（P0-1③，读时算 game_rankings 走势，零 ST）。

核心验证：
- 近窗口检出的 SLG 新品按走势分层：起飞（climbing）列明细 / 掉榜（dropped）列明细 / 在榜计数
- 只看 SLG（live is_slg 过滤）：非 SLG 新品不进卡
- 窗口内无 SLG 新品 → None（不发空卡）
- 中文夹具（CJK 纪律）
"""
import importlib
from datetime import timedelta

import pytest

SLG_PUB = "Century Games Pte. Ltd."   # 种子里可靠命中 is_slg=True
NON_SLG_PUB = "Supercell"


def _live(mod):
    return importlib.import_module(mod)


async def _seed_ranks(app_id, points, country="US", platform="ios", chart_type="grossing"):
    database = _live("app.database")
    GameRanking = _live("app.models.game").GameRanking
    async with database.AsyncSessionLocal() as db:
        for date, rank in points:
            db.add(GameRanking(app_id=app_id, date=date, rank=rank, country=country,
                               platform=platform, chart_type=chart_type,
                               name=f"游戏{app_id}", publisher=SLG_PUB))
        await db.commit()


async def _seed_log(app_id, name, rank, publisher, detected_days_ago, as_of,
                    country="US", platform="ios", subgenre_cn=None):
    database = _live("app.database")
    MarketNewcomerLog = _live("app.models.newcomer").MarketNewcomerLog
    now = database.utcnow_naive()
    async with database.AsyncSessionLocal() as db:
        db.add(MarketNewcomerLog(
            country=country, platform=platform, app_id=app_id, chart_type="grossing",
            as_of=as_of, rank=rank, name=name, publisher=publisher, subgenre_cn=subgenre_cn,
            first_detected_at=now - timedelta(days=detected_days_ago)))
        await db.commit()


def _dates(now, offsets):
    return [((now - timedelta(days=o)).strftime("%Y-%m-%d")) for o in offsets]


@pytest.mark.asyncio
async def test_weekly_review_layers_climbing_dropped(client):
    """起飞 + 掉榜各进对应段；非 SLG 不计；在榜存活计数。"""
    ra = _live("app.services.release_alerts")
    now = _live("app.database").utcnow_naive()
    d5, d3, d1 = _dates(now, [5, 3, 1])  # 三个近日快照

    # 起飞：检出 #60 → 现 #40（accumulate ↑20）；带中文子品类，验可读性标签
    await _seed_ranks("riser", [(d5, 60), (d3, 50), (d1, 40)])
    await _seed_log("riser", "起飞新品", 60, SLG_PUB, 5, d5, subgenre_cn="数字门SLG")
    # 掉榜：检出 #45、d3 还在 #48，d1 消失（combo 最新 d1 无它）
    await _seed_ranks("faded", [(d5, 45), (d3, 48)])
    await _seed_log("faded", "掉榜新品", 45, SLG_PUB, 5, d5)
    # 在榜存活：检出 #30、现 #31（基本持平，未爬未掉）
    await _seed_ranks("steady", [(d5, 30), (d1, 31)])
    await _seed_log("steady", "存活新品", 30, SLG_PUB, 5, d5)
    # 非 SLG：不该进卡
    await _seed_ranks("nonslg", [(d5, 10), (d1, 8)])
    await _seed_log("nonslg", "非SLG新品", 10, NON_SLG_PUB, 5, d5)

    card = await ra.build_weekly_newcomer_review(days=30, cap=8)
    assert card is not None
    title, text = card
    assert "SLG 新品周察" in title
    # 3 款 SLG（非 SLG 被 live is_slg 过滤掉）
    assert "检出 **3** 款 SLG 新品" in text
    # 起飞段含 riser、掉榜段含 faded、非 SLG 不出现
    assert "起飞新品" in text and "🚀 起飞" in text
    assert "掉榜新品" in text and "✝️ 已掉榜" in text
    assert "非SLG新品" not in text
    # 起飞明细：白话排名「#60 → **#40**，X 天涨 20 名」+ 中文子品类标签 + 卡片说明句
    assert "#60 → **#40**" in text and "涨 20 名" in text
    assert "· 数字门SLG" in text
    assert "畅销榜排名" in text


@pytest.mark.asyncio
async def test_weekly_review_none_when_no_slg(client):
    """窗口内只有非 SLG 新品 → None（不发空卡）。"""
    ra = _live("app.services.release_alerts")
    now = _live("app.database").utcnow_naive()
    d5, d1 = _dates(now, [5, 1])
    await _seed_ranks("nonslg", [(d5, 10), (d1, 8)], )
    await _seed_log("nonslg", "非SLG新品", 10, NON_SLG_PUB, 5, d5)
    card = await ra.build_weekly_newcomer_review(days=30, cap=8)
    assert card is None


@pytest.mark.asyncio
async def test_weekly_review_excludes_out_of_window(client):
    """检出早于窗口的新品不计（first_detected_at 窗口过滤）。"""
    ra = _live("app.services.release_alerts")
    now = _live("app.database").utcnow_naive()
    old = (now - timedelta(days=40)).strftime("%Y-%m-%d")
    await _seed_ranks("ancient", [(old, 20)])
    await _seed_log("ancient", "窗口外老品", 20, SLG_PUB, 40, old)  # 40 天前检出
    card = await ra.build_weekly_newcomer_review(days=30, cap=8)
    assert card is None


@pytest.mark.asyncio
async def test_weekly_review_includes_archived_slg_when_live_miss(client):
    """存档 is_slg 聚合兜底：本地化 publisher 串 live is_slg 命不中，但该行检出时
    （别的 combo 判定传播）已存 is_slg=1 → 仍进周察卡，不再被 live 过滤漏掉。"""
    database = _live("app.database")
    ra = _live("app.services.release_alerts")
    MarketNewcomerLog = _live("app.models.newcomer").MarketNewcomerLog
    now = database.utcnow_naive()
    detect_day, today = _dates(now, [3, 0])
    # 检出 #80 → 现 #40：climbing，进「起飞」明细
    await _seed_ranks("w_kr01", [(detect_day, 80), (today, 40)])
    async with database.AsyncSessionLocal() as db:
        db.add(MarketNewcomerLog(
            country="US", platform="ios", app_id="w_kr01", chart_type="grossing",
            as_of=detect_day, rank=80, name="라스트 퍼리: 서바이벌",
            publisher="스타유니온",  # live is_slg miss（韩文串）
            is_slg=True,             # 存档已判 SLG（跨 combo 传播/落库记忆）
            first_detected_at=now - timedelta(days=3)))
        await db.commit()
    card = await ra.build_weekly_newcomer_review(days=7, cap=5)
    assert card is not None
    _, text = card
    assert "라스트 퍼리" in text and "🚀" in text
