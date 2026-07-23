"""发现层·人工线报快速分诊（切片1）测试。

DB 相关用例一律用 `app` fixture（临时库）+ **函数内 import app.***，避免顶层 import 绑默认
dev 库污染 backend/slg_research.db（本项目持久坑）。零外网：coverage 短路用例不出网；unknown
用例 monkeypatch enrich/resolve/classify 拦截。
"""


def test_normalize_tip_recognizes_forms():
    from app.services.discovery_triage import normalize_tip
    assert normalize_tip("com.codex.lastduo") == {"app_id": "com.codex.lastduo", "platform": "android"}
    assert normalize_tip(
        "https://play.google.com/store/apps/details?id=com.codex.lastduo&hl=en"
    ) == {"app_id": "com.codex.lastduo", "platform": "android"}
    assert normalize_tip("1234567890") == {"app_id": "1234567890", "platform": "ios"}
    assert normalize_tip(
        "https://apps.apple.com/us/app/x/id1234567890"
    ) == {"app_id": "1234567890", "platform": "ios"}
    # 认不出：纯文字、空、抽不出 id 的链接
    assert normalize_tip("just some game name") is None
    assert normalize_tip("") is None
    assert normalize_tip("https://example.com/foo") is None


def test_no_sensor_tower_import():
    """零 ST 硬约束钉成可测断言：发现层模块不得 **import** Sensor Tower。
    只查真实 import 行（from/import），不误伤文档字符串里对该约束的说明。"""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    for rel in ("services/discovery_triage.py", "routers/discovery.py"):
        src = (root / rel).read_text(encoding="utf-8")
        import_lines = "\n".join(
            l for l in src.splitlines() if re.match(r"\s*(from|import)\b", l))
        low = import_lines.lower()
        assert "sensor_tower" not in low, f"{rel} import 了 sensor_tower（违反零 ST 铁律）"
        assert "sensortower" not in low and "st_client" not in low


async def test_coverage_tracked_shortcircuits(app, monkeypatch):
    """已追踪 app → coverage=tracked 且短路，绝不出外网（enrich 被拦成炸弹以证明未调用）。"""
    from app.services import discovery_triage as dt
    from app.database import AsyncSessionLocal
    from app.models.game import Game

    async with AsyncSessionLocal() as db:
        db.add(Game(app_id="com.tracked.slg", name="Tracked SLG", publisher="X"))
        await db.commit()

    async def _boom(*a, **k):
        raise AssertionError("已追踪 app 不该触发外网富化")
    monkeypatch.setattr(dt, "enrich_fields", _boom)

    res = await dt.triage("com.tracked.slg")
    assert res["coverage"] == "tracked"
    assert "enrich" not in res


async def test_ignored_app(app):
    """忽略名单命中的 app → coverage=ignored（优先级最高，先于 tracked/detected）。"""
    from app.services import discovery_triage as dt
    from app.database import AsyncSessionLocal
    from app.models.publisher import PublisherIgnore

    async with AsyncSessionLocal() as db:
        db.add(PublisherIgnore(kind="app_id", value="com.meme.ignored"))
        await db.commit()

    res = await dt.triage("com.meme.ignored")
    assert res["coverage"] == "ignored"


async def test_unknown_builds_draft(app, monkeypatch):
    """未追踪（Last Duo 类）→ 零 ST 溯源出建档草稿：反解开发者账号 + 子品类 + pin + 雷达账号。"""
    from app.services import discovery_triage as dt

    async def _enrich(app_id, country, platform):
        return {"name": "Last Duo: Survival", "genre": "Simulation",
                "description": "post-apocalypse zombie survival base building extraction with a dog",
                "enrich_source": "gp"}

    async def _resolve_gp(pkg):
        return {"artist_id": "8382505387204488895", "artist_name": "BTPlay",
                "app_name": "Last Duo: Survival"}

    async def _classify(name, genre, description):
        return "基地建设SLG"

    monkeypatch.setattr(dt, "enrich_fields", _enrich)
    monkeypatch.setattr(dt, "resolve_gp_developer_for_package", _resolve_gp)
    monkeypatch.setattr(dt, "classify_subgenre", _classify)

    res = await dt.triage("com.codex.lastduo")
    assert res["recognized"] is True
    assert res["platform"] == "android"
    assert res["coverage"] == "unknown"
    assert res["developer_account"]["artist_id"] == "8382505387204488895"
    assert res["subgenre_cn"] == "基地建设SLG"
    assert res["is_slg_core"] is True
    draft = res["draft_entity"]
    assert draft["pin"] == {"app_id": "com.codex.lastduo", "platform": "android"}
    assert draft["is_slg"] is True
    assert draft["radar_account"] == {"artist_id": "8382505387204488895", "platform": "gp"}
    assert draft["name"] == "BTPlay"


async def test_unrecognized_tip(app):
    from app.services import discovery_triage as dt
    res = await dt.triage("some new zombie SLG I saw somewhere")
    assert res["recognized"] is False
    assert "hint" in res
