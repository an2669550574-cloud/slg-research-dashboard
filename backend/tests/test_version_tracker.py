"""tracked iOS games 版本变更追踪（需求② / ADR 0003）。

验收：首次填基线不算变更 / 版本变了写 history+更新 Game / 无变更 no-op /
mock 模式 no-op / Android 跳过 / ios_track_id 优先走 bulk / 包名无 trackId 留白 /
digest「版本更新」段。中文游戏名（CJK 硬规则）。
"""
import pytest
from sqlalchemy import select


async def _add_game(app_id, name, platform="ios", version=None, version_date=None,
                    ios_track_id=None, publisher=None):
    from app.database import AsyncSessionLocal
    from app.models.game import Game
    async with AsyncSessionLocal() as db:
        db.add(Game(app_id=app_id, name=name, platform=platform, version=version,
                    version_date=version_date, ios_track_id=ios_track_id, publisher=publisher))
        await db.commit()


@pytest.mark.asyncio
async def test_first_run_sets_baseline_no_change(app, monkeypatch):
    """首次（version=NULL）→ 填基线、不算变更、不写 history（防上线刷屏）。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.game import Game
    from app.models.history import GameHistory
    from app.services import version_tracker as vt
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)

    async def fake_bulk(ids, country="us"):
        return {"111": {"version": "2.0.1", "current_version_date": "2026-06-20", "release_notes": "修复"}}
    monkeypatch.setattr(vt, "fetch_apps_bulk", fake_bulk)

    await _add_game("111", "万国觉醒")
    assert await vt.check_tracked_versions() == []
    async with AsyncSessionLocal() as db:
        g = (await db.execute(select(Game).where(Game.app_id == "111"))).scalar_one()
        h = (await db.execute(select(GameHistory))).scalars().all()
    assert g.version == "2.0.1" and g.version_date == "2026-06-20"
    assert h == []   # 基线不写 history


@pytest.mark.asyncio
async def test_version_change_writes_history_and_updates(app, monkeypatch):
    """版本变了 → 写 game_histories(version) + 更新 Game 当前值 + 返回结构化变更。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.game import Game
    from app.models.history import GameHistory
    from app.services import version_tracker as vt
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "TAISHI_API_KEY", "")  # 隔离 LLM 摘要，本测试只验版本检测/history

    async def fake_bulk(ids, country="us"):
        return {"111": {"version": "2.1.0", "current_version_date": "2026-06-26", "release_notes": "新赛季开启"}}
    monkeypatch.setattr(vt, "fetch_apps_bulk", fake_bulk)

    await _add_game("111", "万国觉醒", version="2.0.1", version_date="2026-06-20")
    changes = await vt.check_tracked_versions()
    assert len(changes) == 1
    assert (changes[0]["old"], changes[0]["new"], changes[0]["name"]) == ("2.0.1", "2.1.0", "万国觉醒")
    async with AsyncSessionLocal() as db:
        g = (await db.execute(select(Game).where(Game.app_id == "111"))).scalar_one()
        h = (await db.execute(select(GameHistory).where(GameHistory.event_type == "version"))).scalars().all()
    assert g.version == "2.1.0" and g.version_date == "2026-06-26"
    assert len(h) == 1
    assert "2.0.1 → 2.1.0" in h[0].title
    assert h[0].event_date == "2026-06-26" and h[0].source == "appstore"
    assert h[0].description == "新赛季开启"


@pytest.mark.asyncio
async def test_no_change_is_noop(app, monkeypatch):
    """版本没变 → 不写 history、不返回变更。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.history import GameHistory
    from app.services import version_tracker as vt
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)

    async def fake_bulk(ids, country="us"):
        return {"111": {"version": "2.0.1", "current_version_date": "2026-06-20"}}
    monkeypatch.setattr(vt, "fetch_apps_bulk", fake_bulk)

    await _add_game("111", "万国觉醒", version="2.0.1", version_date="2026-06-20")
    assert await vt.check_tracked_versions() == []
    async with AsyncSessionLocal() as db:
        assert (await db.execute(select(GameHistory))).scalars().all() == []


@pytest.mark.asyncio
async def test_mock_mode_noop(app, monkeypatch):
    """USE_MOCK_DATA 下整体 no-op，不打真 iTunes。"""
    from app.config import settings
    from app.services import version_tracker as vt
    monkeypatch.setattr(settings, "USE_MOCK_DATA", True)
    called = False

    async def fake_bulk(ids, country="us"):
        nonlocal called
        called = True
        return {}
    monkeypatch.setattr(vt, "fetch_apps_bulk", fake_bulk)

    await _add_game("111", "万国觉醒")
    assert await vt.check_tracked_versions() == []
    assert called is False


@pytest.mark.asyncio
async def test_android_skipped(app, monkeypatch):
    """Android（GP 页无版本源）不查——platform 过滤。"""
    from app.config import settings
    from app.services import version_tracker as vt
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    seen = {}

    async def fake_bulk(ids, country="us"):
        seen["ids"] = ids
        return {}
    monkeypatch.setattr(vt, "fetch_apps_bulk", fake_bulk)

    await _add_game("com.x.y", "安卓游戏", platform="android")
    assert await vt.check_tracked_versions() == []
    assert "ids" not in seen   # 没有 iOS tracked game → 提前 return


@pytest.mark.asyncio
async def test_ios_track_id_takes_priority(app, monkeypatch):
    """GP 包名 app_id + ios_track_id → 用 trackId 走 bulk lookup（不靠包名）。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.game import Game
    from app.services import version_tracker as vt
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    seen = {}

    async def fake_bulk(ids, country="us"):
        seen["ids"] = ids
        return {"1354260888": {"version": "1.1.8", "current_version_date": "2026-06-20"}}
    monkeypatch.setattr(vt, "fetch_apps_bulk", fake_bulk)

    await _add_game("com.lilithgames.rok", "Rise of Kingdoms", ios_track_id="1354260888")
    assert await vt.check_tracked_versions() == []      # 基线
    assert seen["ids"] == ["1354260888"]                # 用 trackId 查，不是包名
    async with AsyncSessionLocal() as db:
        g = (await db.execute(select(Game).where(Game.app_id == "com.lilithgames.rok"))).scalar_one()
    assert g.version == "1.1.8"


@pytest.mark.asyncio
async def test_package_without_trackid_skipped(app, monkeypatch):
    """GP 包名 + 无 ios_track_id → 没有可用 trackId → 不查、不追踪（诚实留白）。"""
    from app.config import settings
    from app.services import version_tracker as vt
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    seen = {}

    async def fake_bulk(ids, country="us"):
        seen["ids"] = ids
        return {}
    monkeypatch.setattr(vt, "fetch_apps_bulk", fake_bulk)

    await _add_game("com.century.games.warpath", "Warpath")  # 包名，未补 trackId
    assert await vt.check_tracked_versions() == []
    assert "ids" not in seen   # tid_map 空 → 提前 return，没调 bulk


def test_digest_renders_version_section():
    """build_daily_digest 把版本变更拼成全局「版本更新」段；纯版本日也发卡。"""
    from app.services.release_alerts import build_daily_digest
    changes = [{"app_id": "111", "name": "万国觉醒", "old": "2.0.1", "new": "2.1.0", "date": "2026-06-26"}]
    res = build_daily_digest([], "2026-06-26", version_changes=changes)
    assert res is not None
    _, body, _ = res
    assert "版本更新" in body
    assert "万国觉醒" in body and "2.0.1 → 2.1.0" in body


@pytest.mark.asyncio
async def test_version_change_includes_notes_summary(app, monkeypatch):
    """版本变更带 LLM 一句话实质摘要（E）→ changes[].notes_cn 有值、原始 notes 拿得到、
    中转键 _notes 不泄漏，digest 渲染 📝 子行。"""
    from app.config import settings
    from app.services import version_tracker as vt
    from app.services.release_alerts import build_daily_digest
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)

    async def fake_bulk(ids, country="us"):
        return {"111": {"version": "2.1.0", "current_version_date": "2026-06-26",
                        "release_notes": "全新赛季『冰封王座』开启，新增 3 名英雄"}}

    async def fake_sum(name, old_v, new_v, notes):
        assert name == "万国觉醒" and (old_v, new_v) == ("2.0.1", "2.1.0")
        assert "冰封王座" in notes           # 拿到原始 release_notes
        return "新赛季冰封王座 + 3 英雄"
    monkeypatch.setattr(vt, "fetch_apps_bulk", fake_bulk)
    monkeypatch.setattr(vt, "_summarize_notes", fake_sum)

    await _add_game("111", "万国觉醒", version="2.0.1", version_date="2026-06-20")
    changes = await vt.check_tracked_versions()
    assert changes[0]["notes_cn"] == "新赛季冰封王座 + 3 英雄"
    assert "_notes" not in changes[0]        # 中转键不泄漏进返回结构

    _, body, _ = build_daily_digest([], "2026-06-26", version_changes=changes)
    assert "📝 新赛季冰封王座 + 3 英雄" in body


@pytest.mark.asyncio
async def test_summarize_notes_noop_without_key(monkeypatch):
    """无 TAISHI_API_KEY → _summarize_notes 直接 None（不打网关），digest 优雅降级只显版本号。"""
    from app.config import settings
    from app.services import version_tracker as vt
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "TAISHI_API_KEY", "")
    assert await vt._summarize_notes("万国觉醒", "2.0", "2.1", "新赛季开启") is None


@pytest.mark.asyncio
async def test_summarize_notes_empty_content_warns_not_silent(monkeypatch, caplog):
    """LLM 返回空内容（真实故障：思考型模型 reasoning 烧光 max_tokens →
    finish_reason='length' + content=''，prod 2026-07-03 实测）→ 返回 None 且**打
    warning**，不再静默伪装成「无摘要」。"""
    import logging
    from types import SimpleNamespace as NS
    from app.config import settings
    from app.services import version_tracker as vt
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "TAISHI_API_KEY", "test-key")

    class FakeCompletions:
        async def create(self, **kwargs):
            # 复刻 prod 故障响应形状：choices 有、content 空、finish_reason=length。
            return NS(choices=[NS(finish_reason="length",
                                  message=NS(content=""))])

    fake_client = NS(chat=NS(completions=FakeCompletions()))
    monkeypatch.setattr(vt.llm_gateway, "get_client", lambda: fake_client)

    with caplog.at_level(logging.WARNING, logger="app.services.version_tracker"):
        out = await vt._summarize_notes("Lords Mobile", "2.196", "2.197",
                                        "Football fever has gripped the Kingdoms")
    assert out is None
    assert any("summarize empty" in r.message and "length" in r.message
               for r in caplog.records)   # 静默失败必须可见


def test_version_lines_render_notes_subline():
    """build_version_lines：notes_cn 有值补 📝 子行；无则只显版本号不加噪。"""
    from app.services.release_alerts import build_version_lines
    lines = build_version_lines([
        {"name": "万国觉醒", "old": "2.0.1", "new": "2.1.0", "date": "2026-06-26",
         "notes_cn": "新赛季冰封王座"},
        {"name": "口袋奇兵", "old": "1.0", "new": "1.1", "date": "2026-06-26"},  # 无 notes_cn
    ], cap=10)
    assert len(lines) == 2
    assert "🆙 **万国觉醒**" in lines[0] and "\n   📝 新赛季冰封王座" in lines[0]
    assert "📝" not in lines[1]   # 无摘要不加子行
