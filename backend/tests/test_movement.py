"""竞品异动检测。conftest 每个 test 重载 app.* —— import 放函数内。

SLG 判定走发行商白名单：Century Games=SLG，Supercell=非SLG（见 slg_publishers）。
"""
import logging
import pytest

SLG_PUB = "Century Games Pte. Ltd."
NON_SLG_PUB = "Supercell"


async def _seed(date, rows, country="US", platform="ios"):
    """rows: list of (app_id, rank, revenue, publisher)。name 用 app_id。"""
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


def _errors(caplog):
    return [r for r in caplog.records
            if r.levelno == logging.ERROR and "[COMPETITOR-MOVEMENT]" in r.getMessage()]


@pytest.mark.asyncio
async def test_new_entrant_fires_single_alert(client, caplog):
    from app.services.movement import detect_and_alert_movement
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-16", [("a", 1, None, SLG_PUB), ("newcomer", 3, None, SLG_PUB)])

    with caplog.at_level(logging.INFO, logger="app.services.movement"):
        s = await detect_and_alert_movement("US", "ios", "2026-05-16")

    assert s["prev_date"] == "2026-05-15"
    assert [e["name"] for e in s["new_entrants"]] == ["newcomer"]
    assert s["new_entrants"][0]["cur_rank"] == 3
    assert len(_errors(caplog)) == 1, "多条异动也只发一条 Sentry 事件"


@pytest.mark.asyncio
async def test_surge_within_topn(client, caplog):
    from app.services.movement import detect_and_alert_movement
    # 昨日 #14 仍在 TopN(15) 内 → 今日 #14→#3 判窜升；若昨日 #16(>TopN) 则应判 new_entrant。
    await _seed("2026-05-15", [("a", 14, None, SLG_PUB)])
    await _seed("2026-05-16", [("a", 3, None, SLG_PUB)])  # 14->3，升11 ≥ 阈值10

    with caplog.at_level(logging.INFO, logger="app.services.movement"):
        s = await detect_and_alert_movement("US", "ios", "2026-05-16")

    assert len(s["surges"]) == 1
    assert s["surges"][0]["name"] == "a"
    assert s["surges"][0]["prev_rank"] == 14 and s["surges"][0]["cur_rank"] == 3
    assert not s["new_entrants"]
    assert len(_errors(caplog)) == 1


@pytest.mark.asyncio
async def test_drop_out_of_topn(client, caplog):
    from app.services.movement import detect_and_alert_movement
    await _seed("2026-05-15", [("a", 5, None, SLG_PUB)])
    await _seed("2026-05-16", [("a", 40, None, SLG_PUB)])  # 跌出 Top20

    with caplog.at_level(logging.INFO, logger="app.services.movement"):
        s = await detect_and_alert_movement("US", "ios", "2026-05-16")

    assert len(s["drops"]) == 1
    assert s["drops"][0]["name"] == "a"
    assert s["drops"][0]["prev_rank"] == 5 and s["drops"][0]["cur_rank"] == 40
    assert len(_errors(caplog)) == 1


@pytest.mark.asyncio
async def test_revenue_spike(client, caplog):
    from app.services.movement import detect_and_alert_movement
    await _seed("2026-05-15", [("a", 5, 100_000.0, SLG_PUB)])
    await _seed("2026-05-16", [("a", 5, 250_000.0, SLG_PUB)])  # +150% ≥ 50%

    with caplog.at_level(logging.INFO, logger="app.services.movement"):
        s = await detect_and_alert_movement("US", "ios", "2026-05-16")

    assert len(s["revenue_spikes"]) == 1 and s["revenue_spikes"][0]["name"] == "a"
    assert s["revenue_spikes"][0]["pct"] == pytest.approx(150.0)
    assert not s["surges"], "名次没动，不该报窜升"
    assert len(_errors(caplog)) == 1


@pytest.mark.asyncio
async def test_non_slg_movement_ignored(client, caplog):
    from app.services.movement import detect_and_alert_movement
    await _seed("2026-05-15", [("slg", 1, None, SLG_PUB)])
    await _seed("2026-05-16", [("slg", 1, None, SLG_PUB), ("coc", 2, None, NON_SLG_PUB)])

    with caplog.at_level(logging.INFO, logger="app.services.movement"):
        s = await detect_and_alert_movement("US", "ios", "2026-05-16")

    assert s["new_entrants"] == [] and s["surges"] == []
    assert _errors(caplog) == [], "非 SLG 异动不告警"


@pytest.mark.asyncio
async def test_no_previous_day_no_alert(client, caplog):
    from app.services.movement import detect_and_alert_movement
    await _seed("2026-05-16", [("a", 1, None, SLG_PUB)])

    with caplog.at_level(logging.INFO, logger="app.services.movement"):
        s = await detect_and_alert_movement("US", "ios", "2026-05-16")

    assert s["prev_date"] is None
    assert _errors(caplog) == [], "冷库无可比对，绝不发空告警"


@pytest.mark.asyncio
async def test_empty_today_marks_stale_no_drops(client, caplog):
    """ST 配额耗尽场景:今日 game_rankings 为空时,不能把昨日 TopN SLG 全员
    错报为"跌出 TOP"。返回 today_missing=True、drops 列表保持为空。"""
    from app.services.movement import detect_movement
    # 昨天有 5 个 SLG 在榜
    await _seed("2026-05-15", [
        ("a", 1, None, SLG_PUB), ("b", 2, None, SLG_PUB), ("c", 3, None, SLG_PUB),
        ("d", 4, None, SLG_PUB), ("e", 5, None, SLG_PUB),
    ])
    # 今天什么都没同步进来
    s = await detect_movement("US", "ios", "2026-05-16")

    assert s["prev_date"] == "2026-05-15"
    assert s["today_missing"] is True
    assert s["drops"] == [], "今日缺数据时绝不报跌出"
    assert s["new_entrants"] == [] and s["surges"] == [] and s["revenue_spikes"] == []


@pytest.mark.asyncio
async def test_sparse_today_marks_stale(client):
    """今日行数远少于昨日(< 30% 且 < 10)时也判为不完整,跳过对比。"""
    from app.services.movement import detect_movement
    # 昨天 50 个
    await _seed("2026-05-15", [(f"app{i}", i, None, SLG_PUB) for i in range(1, 51)])
    # 今天只同步进来 3 个(同步中途失败)
    await _seed("2026-05-16", [(f"app{i}", i, None, SLG_PUB) for i in range(1, 4)])

    s = await detect_movement("US", "ios", "2026-05-16")
    assert s["today_missing"] is True
    assert s["drops"] == []


@pytest.mark.asyncio
async def test_full_today_does_not_mark_stale(client):
    """今日数据完整时不应被误判为 stale。"""
    from app.services.movement import detect_movement
    await _seed("2026-05-15", [(f"app{i}", i, None, SLG_PUB) for i in range(1, 21)])
    # 今日同样规模——一切正常,正常走对比逻辑(本例无异动,但 today_missing 必须 False)
    await _seed("2026-05-16", [(f"app{i}", i, None, SLG_PUB) for i in range(1, 21)])

    s = await detect_movement("US", "ios", "2026-05-16")
    assert s["today_missing"] is False


@pytest.mark.asyncio
async def test_disabled_flag_short_circuits(client, caplog, monkeypatch):
    from app.services import movement
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-16", [("a", 1, None, SLG_PUB), ("new", 2, None, SLG_PUB)])
    monkeypatch.setattr(movement.settings, "COMPETITOR_ALERT_ENABLED", False)

    with caplog.at_level(logging.INFO, logger="app.services.movement"):
        s = await movement.detect_and_alert_movement("US", "ios", "2026-05-16")

    assert s["prev_date"] is None and s["new_entrants"] == []
    assert _errors(caplog) == []


# ── 回归门控（is_reentry，P1.4）：老 SLG 短暂跌出 TopN 又回来 ≠ 真「空降」 ──

@pytest.mark.asyncio
async def test_reentry_within_window_flagged(client):
    """窗口内曾在 TopN、上一可用日跌出、今日回来 → new_entrant 标 is_reentry=True。"""
    from app.services.movement import detect_movement
    await _seed("2026-05-10", [("a", 1, None, SLG_PUB), ("r", 5, None, SLG_PUB)])   # r 曾在 TopN
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB)])                            # r 跌出
    await _seed("2026-05-16", [("a", 1, None, SLG_PUB), ("r", 8, None, SLG_PUB)])   # r 回来
    s = await detect_movement("US", "ios", "2026-05-16")
    ne = {e["name"]: e for e in s["new_entrants"]}
    assert "r" in ne and ne["r"]["is_reentry"] is True


@pytest.mark.asyncio
async def test_true_first_entrant_not_reentry(client):
    """从未在 TopN 出现过的真首发 → is_reentry=False（空降照旧）。"""
    from app.services.movement import detect_movement
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-16", [("a", 1, None, SLG_PUB), ("fresh", 4, None, SLG_PUB)])
    s = await detect_movement("US", "ios", "2026-05-16")
    ne = {e["name"]: e for e in s["new_entrants"]}
    assert ne["fresh"]["is_reentry"] is False


@pytest.mark.asyncio
async def test_reentry_outside_window_not_flagged(client):
    """曾上 TopN 但在回看窗(默认 30 天)之外 → 不算回归，当真首发处理（旧事件重新计新闻性）。"""
    from app.services.movement import detect_movement
    await _seed("2026-03-01", [("a", 1, None, SLG_PUB), ("old", 5, None, SLG_PUB)])  # 远早于窗
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-16", [("a", 1, None, SLG_PUB), ("old", 8, None, SLG_PUB)])
    s = await detect_movement("US", "ios", "2026-05-16")
    ne = {e["name"]: e for e in s["new_entrants"]}
    assert ne["old"]["is_reentry"] is False


@pytest.mark.asyncio
async def test_reentry_window_zero_disables(client, monkeypatch):
    """COMPETITOR_REENTRY_WINDOW_DAYS=0 关回归判定 → 真回归也按 is_reentry=False。"""
    from app.services import movement
    monkeypatch.setattr(movement.settings, "COMPETITOR_REENTRY_WINDOW_DAYS", 0)
    await _seed("2026-05-10", [("a", 1, None, SLG_PUB), ("r", 5, None, SLG_PUB)])
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-16", [("a", 1, None, SLG_PUB), ("r", 8, None, SLG_PUB)])
    s = await movement.detect_movement("US", "ios", "2026-05-16")
    ne = {e["name"]: e for e in s["new_entrants"]}
    assert ne["r"]["is_reentry"] is False


@pytest.mark.asyncio
async def test_reentry_only_counts_topn_history(client):
    """窗口内只在榜尾(>TopN)出现过、从未进 TopN → 仍是真首发，不误标回归。"""
    from app.services.movement import detect_movement
    await _seed("2026-05-10", [("a", 1, None, SLG_PUB), ("climber", 35, None, SLG_PUB)])  # 35>topn(15)
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-16", [("a", 1, None, SLG_PUB), ("climber", 6, None, SLG_PUB)])   # 首进 TopN
    s = await detect_movement("US", "ios", "2026-05-16")
    ne = {e["name"]: e for e in s["new_entrants"]}
    assert ne["climber"]["is_reentry"] is False


# ── 连涨趋势（sustained climb）：补 surge 单日阈值盲区，多日稳步累计爬升 ──
# 默认 win=5 / min_drop=10 / climb_topn=30 / min_snaps=3 / RANK_JUMP=10。
# 稳定锚（anchor #1 每天）避免 today_missing 稀疏闸门，且自身 net=0 不误报连涨。

async def _seed_days(app_id, day_rank, publisher=SLG_PUB, country="US", platform="ios"):
    """按 {date: rank} 逐日 seed 一个 app（外加稳定锚 anchor #1）。"""
    for date, rank in day_rank.items():
        await _seed(date, [("anchor", 1, None, SLG_PUB), (app_id, rank, None, publisher)],
                    country=country, platform=platform)


@pytest.mark.asyncio
async def test_sustained_climb_detected(client):
    """War and Order 式稳步爬：#40→#38→#35→#32→#28，单日最多升 4（<10 不触 surge）、
    5 天累计升 12（≥min_drop）、今日窗口新高 → 命中连涨。"""
    from app.services.movement import detect_movement
    await _seed_days("wao", {"2026-05-12": 40, "2026-05-13": 38, "2026-05-14": 35,
                             "2026-05-15": 32, "2026-05-16": 28})
    s = await detect_movement("US", "ios", "2026-05-16")
    cl = {e["name"]: e for e in s["climbs"]}
    assert "wao" in cl, "稳步连涨应命中"
    assert cl["wao"]["start_rank"] == 40 and cl["wao"]["cur_rank"] == 28
    assert cl["wao"]["span_days"] == 5 and cl["wao"]["start_date"] == "2026-05-12"
    # 单日够不到阈值 → 不该同时被 surge/new_entrant 报（今日 #28>TopN15 也不在两段）
    assert [e["name"] for e in s["surges"]] == []
    assert "wao" not in {e["name"] for e in s["new_entrants"]}


@pytest.mark.asyncio
async def test_climb_excludes_single_day_surge(client):
    """窗口内含单日大跳（#38→#22 升16≥RANK_JUMP）→ 那天本已被 surge 报过，
    连涨段排除，不重复计。"""
    from app.services.movement import detect_movement
    await _seed_days("jump", {"2026-05-12": 40, "2026-05-13": 38, "2026-05-14": 22,
                              "2026-05-15": 20, "2026-05-16": 18})
    s = await detect_movement("US", "ios", "2026-05-16")
    assert [e["name"] for e in s["climbs"]] == [], "含单日 surge 的爬升不进连涨段"


@pytest.mark.asyncio
async def test_climb_excludes_faded_peak(client):
    """曾爬到高位又回落（今日非窗口新高）→ 不算「正在连涨」。
    #40→#33→#27→#30：累计升 10≥阈值、今日 #30≤climb_topn(30) 进候选、无单日 surge，
    但今日 #30 > 窗口最好 #27 → 走 faded-peak 分支排除（今日名次刻意压在 climb_topn 内，
    确保是 faded 逻辑而非 topn 闸门把它挡掉）。"""
    from app.services.movement import detect_movement
    await _seed_days("fade", {"2026-05-13": 40, "2026-05-14": 33,
                              "2026-05-15": 27, "2026-05-16": 30})
    s = await detect_movement("US", "ios", "2026-05-16")
    assert [e["name"] for e in s["climbs"]] == [], "回落中的不算连涨"


@pytest.mark.asyncio
async def test_climb_excludes_vshape_recovery(client):
    """先跌破起点再净回升的 V 形/震荡 → 非「稳步」连涨，即便净上行达标也排除。
    #44→#46(跌破起点)→#40→#36→#30：净升 14≥阈值、今日窗口新高、无单日 surge，但起点 #44
    非窗口最差（#46 更差）→ start_rank != max(ranks) 排除。"""
    from app.services.movement import detect_movement
    await _seed_days("vshape", {"2026-05-12": 44, "2026-05-13": 46, "2026-05-14": 40,
                                "2026-05-15": 36, "2026-05-16": 30})
    s = await detect_movement("US", "ios", "2026-05-16")
    assert [e["name"] for e in s["climbs"]] == [], "V 形回升不算稳步连涨"


@pytest.mark.asyncio
async def test_climb_allows_dip_above_start(client):
    """真实样本 Z Route 式：内部有小抖动（#32→#36）但始终 ≤ 起点 #40 → 仍算稳步连涨。
    起点即窗口最差、今日 #30≤climb_topn(30) 且为窗口新高、无单日 surge。"""
    from app.services.movement import detect_movement
    await _seed_days("zroute", {"2026-05-12": 40, "2026-05-13": 32, "2026-05-14": 36,
                                "2026-05-15": 33, "2026-05-16": 30})
    s = await detect_movement("US", "ios", "2026-05-16")
    cl = {e["name"]: e for e in s["climbs"]}
    assert "zroute" in cl, "起点内小抖动但不跌破起点 → 仍是连涨"
    assert cl["zroute"]["start_rank"] == 40 and cl["zroute"]["cur_rank"] == 30


@pytest.mark.asyncio
async def test_climb_requires_min_snapshots(client):
    """窗口内快照 < min_snaps(3) → 数据太稀疏（次市场/冷启动），跳过不误报。
    只两天：#40→#28，累计够、今日新高，但仅 2 个快照。"""
    from app.services.movement import detect_movement
    await _seed_days("sparse", {"2026-05-14": 40, "2026-05-16": 28})
    s = await detect_movement("US", "ios", "2026-05-16")
    assert [e["name"] for e in s["climbs"]] == [], "快照不足不判连涨"


@pytest.mark.asyncio
async def test_climb_below_min_drop_ignored(client):
    """累计升幅 < min_drop(10) → 日常抖动，不报。#34→#33→#31→#30→#27：累计仅升 7。"""
    from app.services.movement import detect_movement
    await _seed_days("small", {"2026-05-12": 34, "2026-05-13": 33, "2026-05-14": 31,
                               "2026-05-15": 30, "2026-05-16": 27})
    s = await detect_movement("US", "ios", "2026-05-16")
    assert [e["name"] for e in s["climbs"]] == [], "升幅不足不报连涨"


@pytest.mark.asyncio
async def test_climb_non_slg_ignored(client):
    """非 SLG 稳步爬升（如 Summoners War）不进连涨段——用户只要 SLG 竞品动向。"""
    from app.services.movement import detect_movement
    await _seed_days("rpg", {"2026-05-12": 40, "2026-05-13": 38, "2026-05-14": 35,
                             "2026-05-15": 32, "2026-05-16": 28}, publisher=NON_SLG_PUB)
    s = await detect_movement("US", "ios", "2026-05-16")
    assert [e["name"] for e in s["climbs"]] == []


@pytest.mark.asyncio
async def test_climb_beyond_topn_ignored(client):
    """今日名次 > climb_topn(30) → 榜尾长尾，不报（真新高但太靠后无监控价值）。
    #60→#58→#55→#52→#48：稳步、累计升 12，但今日 #48>30。"""
    from app.services.movement import detect_movement
    await _seed_days("tail", {"2026-05-12": 60, "2026-05-13": 58, "2026-05-14": 55,
                              "2026-05-15": 52, "2026-05-16": 48})
    s = await detect_movement("US", "ios", "2026-05-16")
    assert [e["name"] for e in s["climbs"]] == []


@pytest.mark.asyncio
async def test_climb_disabled_flag(client, monkeypatch):
    """COMPETITOR_CLIMB_ENABLED=False → 关闭连涨检测。"""
    from app.services import movement
    monkeypatch.setattr(movement.settings, "COMPETITOR_CLIMB_ENABLED", False)
    await _seed_days("wao", {"2026-05-12": 40, "2026-05-13": 38, "2026-05-14": 35,
                             "2026-05-15": 32, "2026-05-16": 28})
    s = await movement.detect_movement("US", "ios", "2026-05-16")
    assert s["climbs"] == []
