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
    await _seed("2026-05-15", [("a", 18, None, SLG_PUB)])
    await _seed("2026-05-16", [("a", 3, None, SLG_PUB)])  # 18->3，升15 ≥ 阈值10

    with caplog.at_level(logging.INFO, logger="app.services.movement"):
        s = await detect_and_alert_movement("US", "ios", "2026-05-16")

    assert len(s["surges"]) == 1
    assert s["surges"][0]["name"] == "a"
    assert s["surges"][0]["prev_rank"] == 18 and s["surges"][0]["cur_rank"] == 3
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
    await _seed("2026-05-10", [("a", 1, None, SLG_PUB), ("climber", 35, None, SLG_PUB)])  # 35>topn(20)
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-16", [("a", 1, None, SLG_PUB), ("climber", 6, None, SLG_PUB)])   # 首进 TopN
    s = await detect_movement("US", "ios", "2026-05-16")
    ne = {e["name"]: e for e in s["new_entrants"]}
    assert ne["climber"]["is_reentry"] is False
