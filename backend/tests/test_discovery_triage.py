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


def _aret(val):
    async def _f(*a, **k):
        return val
    return _f


def _patch_unknown(dt, monkeypatch, artist_id="123456789", subgenre="基地建设SLG"):
    monkeypatch.setattr(dt, "enrich_fields", _aret(
        {"name": "Foo SLG", "genre": "Simulation", "description": "post-apoc base building",
         "store_url": "https://play.google.com/store/apps/details?id=com.foo.slg",
         "enrich_source": "gp"}))
    monkeypatch.setattr(dt, "resolve_gp_developer_for_package", _aret(
        {"artist_id": artist_id, "artist_name": "Foo Studio", "app_name": "Foo SLG"}))
    monkeypatch.setattr(dt, "classify_subgenre", _aret(subgenre))


# ── 期2 出口B：写 discovery 影子行 ──
async def test_log_tip_writes_discovery_shadow_row(app, monkeypatch):
    from sqlalchemy import select
    from app.services import discovery_triage as dt
    from app.database import AsyncSessionLocal
    from app.models.newcomer import MarketNewcomerLog

    _patch_unknown(dt, monkeypatch)
    res = await dt.log_tip("com.foo.slg")
    assert res["logged"] is True and res["written"] == 1
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(MarketNewcomerLog).where(
            MarketNewcomerLog.chart_type == "discovery"))).scalar_one()
    assert row.app_id == "com.foo.slg" and row.is_slg is True
    assert row.subgenre_cn == "基地建设SLG" and row.platform == "android"
    # 幂等：落库后该 app_id 即在 market_newcomer_log → 再确认时 coverage=detected，短路不重写
    res2 = await dt.log_tip("com.foo.slg")
    assert res2["logged"] is False and "detected" in res2["reason"]


async def test_log_tip_refuses_covered(app, monkeypatch):
    from app.services import discovery_triage as dt
    from app.database import AsyncSessionLocal
    from app.models.game import Game
    async with AsyncSessionLocal() as db:
        db.add(Game(app_id="com.tracked.x", name="Tracked", publisher="P"))
        await db.commit()

    async def _boom(*a, **k):
        raise AssertionError("covered 线报不该出网/落库")
    monkeypatch.setattr(dt, "enrich_fields", _boom)
    res = await dt.log_tip("com.tracked.x")
    assert res["logged"] is False and "coverage=tracked" in res["reason"]


# ── 期2.5 出口A：一键建号 + 挂雷达 ──
async def test_build_entity_from_tip(app, monkeypatch):
    from sqlalchemy import select
    from app.services import discovery_triage as dt
    from app.database import AsyncSessionLocal
    from app.models.publisher import PublisherEntity, PublisherAppId, PublisherItunesArtist

    _patch_unknown(dt, monkeypatch, artist_id="999888777", subgenre="国战SLG")
    res = await dt.build_entity_from_tip("com.foo.slg")
    assert res["built"] is True and res["radar_attached"] is True
    eid = res["entity_id"]
    async with AsyncSessionLocal() as db:
        e = (await db.execute(select(PublisherEntity).where(PublisherEntity.id == eid))).scalar_one()
        pin = (await db.execute(select(PublisherAppId).where(PublisherAppId.entity_id == eid))).scalar_one()
        art = (await db.execute(select(PublisherItunesArtist).where(
            PublisherItunesArtist.entity_id == eid))).scalar_one()
    assert e.name == "Foo Studio" and e.is_slg is True
    assert pin.app_id == "com.foo.slg"
    assert art.artist_id == "999888777" and art.platform == "gp"


async def test_build_entity_skips_overlong_radar_id(app, monkeypatch):
    from app.services import discovery_triage as dt
    # 名称型开发者 id > 30 字符（Just Game 那类）→ 挂不上雷达，但主体照建
    _patch_unknown(dt, monkeypatch, artist_id="SINGAPORE JUST GAME TECHNOLOGY PTE. LTD.")
    res = await dt.build_entity_from_tip("com.foo.slg", name="Just Game")
    assert res["built"] is True
    assert res["radar_attached"] is False and res["radar_skipped"] is not None


async def test_build_entity_refuses_without_name(app, monkeypatch):
    from app.services import discovery_triage as dt
    # 反解不出厂商名 + 未传 name → 拒绝建空壳
    monkeypatch.setattr(dt, "enrich_fields", _aret({"name": "NoDev SLG", "genre": "X", "description": "d"}))
    monkeypatch.setattr(dt, "resolve_gp_developer_for_package", _aret({}))
    monkeypatch.setattr(dt, "classify_subgenre", _aret(None))
    res = await dt.build_entity_from_tip("com.nodev.slg")
    assert res["built"] is False and "厂商名" in res["reason"]


# ── 期2 digest 段：仅维护者卡 ──
def test_build_discovery_lines_render():
    from app.services.release_alerts import build_discovery_lines
    items = [{"app_id": "com.x.slg", "name": "X SLG", "entity": "X Studio", "platform": "android",
              "country": "WW", "genre": "模拟", "subgenre_cn": "基地建设SLG", "summary": "末日基建",
              "store_url": "https://play.google.com/store/apps/details?id=com.x.slg"}]
    lines = build_discovery_lines(items, 8)
    assert lines and "📮" in lines[0] and "X SLG" in lines[0] and "基地建设SLG" in lines[0]


def test_discovery_segment_maintainer_only():
    from app.services.release_alerts import build_daily_digest
    per_combo = [{"country": "US", "platform": "ios", "movement": None,
                  "market": {"newcomers": [{"app_id": "1", "rank": 3, "name": "Anchor",
                                            "publisher": "P", "is_slg": True, "is_reentry": False}]},
                  "publisher": None}]
    items = [{"app_id": "com.x.slg", "name": "X SLG", "entity": "X Studio", "platform": "android",
              "country": "WW", "genre": "模拟", "subgenre_cn": "基地建设SLG", "summary": "末日基建",
              "store_url": "https://play.google.com/store/apps/details?id=com.x.slg"}]
    _, text_m, _ = build_daily_digest(per_combo, "2026-07-23", discovery_items=items, audience="maintainer")
    assert "📮 发现层线报" in text_m
    _, text_l, _ = build_daily_digest(per_combo, "2026-07-23", discovery_items=items, audience="leader")
    assert "📮 发现层线报" not in text_l   # 领导卡不加（减量宪法）
