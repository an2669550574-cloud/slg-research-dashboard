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
async def test_disabled_flag_short_circuits(client, caplog, monkeypatch):
    from app.services import movement
    await _seed("2026-05-15", [("a", 1, None, SLG_PUB)])
    await _seed("2026-05-16", [("a", 1, None, SLG_PUB), ("new", 2, None, SLG_PUB)])
    monkeypatch.setattr(movement.settings, "COMPETITOR_ALERT_ENABLED", False)

    with caplog.at_level(logging.INFO, logger="app.services.movement"):
        s = await movement.detect_and_alert_movement("US", "ios", "2026-05-16")

    assert s["prev_date"] is None and s["new_entrants"] == []
    assert _errors(caplog) == []
