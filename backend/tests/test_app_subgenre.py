"""存量竞品玩法子品类回补（app_subgenre，P1-2）。

验收：
- classify_subgenre：词表内值写入 / 非词表→None / 无描述→None / mock·无 key→None
- classify_pending_app_subgenres：候选=tracked games(全纳入)+有描述 is_slg log 行；
  排除 已分类/已有 subgenre/非 SLG/无描述；写行即「已尝试」(None 也写)、幂等
- _subgenres_for_apps：market_newcomer_log 优先 + app_subgenre fallback
- 中文夹具（CJK 纪律）
"""
import pytest
from sqlalchemy import select

SLG_PUB = "Century Games Pte. Ltd."   # 种子里 is_slg=True
NON_SLG_PUB = "Supercell"


# ── 假 LLM 网关 client（OpenAI 兼容形状）──────────────────────────────────
class _Msg:
    def __init__(self, content): self.content = content
class _Choice:
    def __init__(self, content): self.message = _Msg(content)
class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = {"prompt_tokens": 50, "completion_tokens": 5}
class _Completions:
    def __init__(self, content, counter): self._c, self._n = content, counter
    async def create(self, **kw):
        self._n.append(1)
        return _Resp(self._c)
class _Chat:
    def __init__(self, content, counter): self.completions = _Completions(content, counter)
class _Client:
    def __init__(self, content, counter): self.chat = _Chat(content, counter)


async def _add_game(app_id, name, description, publisher="厂商X"):
    from app.database import AsyncSessionLocal
    from app.models.game import Game
    async with AsyncSessionLocal() as db:
        db.add(Game(app_id=app_id, name=name, description=description, publisher=publisher))
        await db.commit()


async def _add_log(app_id, name, publisher, description="商店描述", subgenre_cn=None):
    from app.database import AsyncSessionLocal
    from app.models.newcomer import MarketNewcomerLog
    async with AsyncSessionLocal() as db:
        db.add(MarketNewcomerLog(country="US", platform="ios", app_id=app_id, as_of="2026-06-27",
                                 name=name, publisher=publisher, description=description,
                                 subgenre_cn=subgenre_cn))
        await db.commit()


async def _add_subgenre(app_id, subgenre_cn, name="老竞品"):
    from app.database import AsyncSessionLocal
    from app.models.newcomer import AppSubgenre
    async with AsyncSessionLocal() as db:
        db.add(AppSubgenre(app_id=app_id, name=name, subgenre_cn=subgenre_cn, source="test"))
        await db.commit()


async def _all_subgenres():
    from app.database import AsyncSessionLocal
    from app.models.newcomer import AppSubgenre
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AppSubgenre))).scalars().all()
    return {r.app_id: r.subgenre_cn for r in rows}


@pytest.mark.asyncio
async def test_classify_subgenre_valid_invalid_nodesc_nokey(app, monkeypatch):
    from app.config import settings
    from app.services import newcomer_i18n as ni
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "TAISHI_API_KEY", "k")

    # 词表内值 → 写入（**中文名不在这里产出**，改由 store_cn_name 查商店）
    monkeypatch.setattr(ni.llm_gateway, "get_client",
                        lambda: _Client('{"subgenre": "数字门SLG"}', []))
    assert await ni.classify_subgenre("Last War", "Games", "跑酷穿门滚雪球") == "数字门SLG"
    # 非词表值 → None
    monkeypatch.setattr(ni.llm_gateway, "get_client",
                        lambda: _Client('{"subgenre": "我编的子品类"}', []))
    assert await ni.classify_subgenre("Weird", "Games", "有描述") is None
    # 无描述 → None（不构造 client、不瞎猜）
    calls: list = []
    monkeypatch.setattr(ni.llm_gateway, "get_client", lambda: _Client('{"subgenre": "塔防"}', calls))
    assert await ni.classify_subgenre("无描述", "Games", None) is None
    assert calls == []
    # 无 key → None
    monkeypatch.setattr(settings, "TAISHI_API_KEY", None)
    assert await ni.classify_subgenre("x", "Games", "desc") is None


@pytest.mark.asyncio
async def test_backfill_candidates_and_gates(app, monkeypatch):
    """候选=tracked games（全纳入）+ 有描述 is_slg log；排除 非SLG/已有subgenre/无描述/已分类。"""
    from app.config import settings
    from app.services import newcomer_i18n as ni
    from app.services.app_subgenre import classify_pending_app_subgenres
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "TAISHI_API_KEY", "k")
    calls: list = []
    monkeypatch.setattr(ni.llm_gateway, "get_client",
                        lambda: _Client('{"subgenre": "基地建设SLG"}', calls))

    await _add_game("tracked1", "追踪竞品", "建避难所招英雄")        # tracked → 纳入
    await _add_log("slg1", "SLG新品", SLG_PUB, description="城建 SLG")  # is_slg → 纳入
    await _add_log("nonslg", "非SLG", NON_SLG_PUB, description="部落冲突")  # 非 SLG → 排除
    await _add_log("hassg", "已分类", SLG_PUB, description="desc", subgenre_cn="国战SLG")  # 已有 → 排除
    await _add_log("nodesc", "无描述", SLG_PUB, description=None)      # 无描述 → 排除
    await _add_subgenre("already", "塔防")                            # 已在 app_subgenre → 排除

    done = await classify_pending_app_subgenres()
    assert done == 2                       # tracked1 + slg1
    assert len(calls) == 2
    got = await _all_subgenres()
    assert got.get("tracked1") == "基地建设SLG"
    assert got.get("slg1") == "基地建设SLG"
    assert "nonslg" not in got and "hassg" not in got and "nodesc" not in got
    assert got.get("already") == "塔防"    # 原样保留、未重分类


@pytest.mark.asyncio
async def test_backfill_writes_none_row_and_idempotent(app, monkeypatch):
    """LLM 给非词表值 → 写 subgenre_cn=None 的行（已尝试标记），二次不再烧 LLM。"""
    from app.config import settings
    from app.services import newcomer_i18n as ni
    from app.services.app_subgenre import classify_pending_app_subgenres
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "TAISHI_API_KEY", "k")
    calls: list = []
    monkeypatch.setattr(ni.llm_gateway, "get_client",
                        lambda: _Client('{"subgenre": "词表外的怪词"}', calls))

    await _add_game("g_none", "分不出的竞品", "很模糊的描述")
    assert await classify_pending_app_subgenres() == 1
    got = await _all_subgenres()
    assert "g_none" in got and got["g_none"] is None   # 写了行、subgenre 为 None
    # 二次：行已存在 → 不再选为候选、不再烧 LLM
    assert await classify_pending_app_subgenres() == 0
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_backfill_noop_mock_and_no_key(app, monkeypatch):
    from app.config import settings
    from app.services.app_subgenre import classify_pending_app_subgenres
    await _add_game("g1", "竞品", "描述")
    from app.config import settings as s
    monkeypatch.setattr(s, "USE_MOCK_DATA", True)
    monkeypatch.setattr(s, "TAISHI_API_KEY", "k")
    assert await classify_pending_app_subgenres() == 0
    monkeypatch.setattr(s, "USE_MOCK_DATA", False)
    monkeypatch.setattr(s, "TAISHI_API_KEY", None)
    assert await classify_pending_app_subgenres() == 0


@pytest.mark.asyncio
async def test_subgenres_for_apps_log_priority_then_fallback(app):
    """market_newcomer_log 有 subgenre 优先；缺的 fallback app_subgenre。"""
    from app.services.release_alerts import _subgenres_for_apps
    await _add_log("l1", "新品", SLG_PUB, subgenre_cn="国战SLG")
    await _add_subgenre("l1", "塔防")     # 同 app 也在 app_subgenre → 不应覆盖 log 的国战SLG
    await _add_subgenre("veteran", "基地建设SLG")   # 只在 app_subgenre → fallback 命中
    got = await _subgenres_for_apps({"l1", "veteran", "unknown"})
    assert got == {"l1": "国战SLG", "veteran": "基地建设SLG"}


def test_store_cn_name_requires_actual_chinese_title():
    """中文名只认商店里**真的本地化过**的标题——「中文区有上架」不等于「标题是中文」。

    2026-07-20 实测：Tribal Wars(InnoGames) 在台港 App Store 有上架，但标题栏就是英文
    `Tribal Wars`。LLM 当时把它译成《部落战争》——那是 Clash of Clans 的知名民间别名，
    领导拿去搜会搜到另一款游戏，比看英文原名更糟。含汉字判据正是拦这一类的。
    """
    from app.services.store_cn_name import has_cjk

    assert has_cjk("火器文明") is True            # App Store 国服官方名
    assert has_cjk("境界守望者") is True           # App Store 台/港官方名
    assert has_cjk("Million Lords：世界征服") is True   # 中英混排也算本地化过
    assert has_cjk("Tribal Wars") is False       # 台港有上架但标题未本地化 → 保留原名
    assert has_cjk("Guns of Glory") is False
    assert has_cjk("") is False
    assert has_cjk(None) is False


@pytest.mark.asyncio
async def test_manual_override_survives_llm_reclassification(app, monkeypatch):
    """人工判定的子品类必须扛得住后续 LLM 重判——这是这套机制存在的全部理由。

    LLM 分类挂在 market_newcomer_log 的**行**上，新检出行会触发重译并按 app_id 回写该 app
    全部行（2026-07-20 实测冲掉了前一天人工改好的 Battle Kiss）。人工判定写进 app_subgenre
    的 source='manual'，LLM 管道碰不到，读取时又最高优先。"""
    from app.services.app_subgenre import (
        set_manual_subgenre, resolve_subgenres, classify_pending_app_subgenres)

    await _add_log("battlekiss", "Battle Kiss", SLG_PUB, subgenre_cn="基地建设SLG")
    # 人工溯源结论：截图实为数值门跑酷 → 数字门SLG
    await set_manual_subgenre("battlekiss", "数字门SLG", name="Battle Kiss")
    assert (await resolve_subgenres(["battlekiss"]))["battlekiss"] == "数字门SLG"

    # 模拟同 app 新检出触发重译：LLM 又把榜行写回「基地建设SLG」
    from app.database import AsyncSessionLocal
    from app.models.newcomer import MarketNewcomerLog
    from sqlalchemy import update
    async with AsyncSessionLocal() as db:
        await db.execute(update(MarketNewcomerLog)
                         .where(MarketNewcomerLog.app_id == "battlekiss")
                         .values(subgenre_cn="基地建设SLG"))
        await db.commit()
    assert (await resolve_subgenres(["battlekiss"]))["battlekiss"] == "数字门SLG", \
        "人工判定被 LLM 重判覆盖了"

    # 回补 drain 也不得重新分类人工行（它把已在本表的 app 整体排除）
    from app.config import settings
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "TAISHI_API_KEY", "k")
    calls: list = []
    from app.services import newcomer_i18n as ni
    monkeypatch.setattr(ni.llm_gateway, "get_client",
                        lambda: _Client('{"subgenre": "塔防"}', calls))
    await classify_pending_app_subgenres()
    assert (await resolve_subgenres(["battlekiss"]))["battlekiss"] == "数字门SLG"


@pytest.mark.asyncio
async def test_resolve_subgenres_three_level_priority(app):
    """三级优先：人工 > 榜行 LLM > 存量回补 LLM。"""
    from app.services.app_subgenre import resolve_subgenres, set_manual_subgenre

    await _add_log("has_log", "有榜行", SLG_PUB, subgenre_cn="国战SLG")
    await _add_subgenre("has_log", "塔防")            # 同 app 也有回补行 → 榜行应胜出
    await _add_subgenre("only_backfill", "基地建设SLG")  # 只有回补行
    await _add_log("manual_wins", "人工优先", SLG_PUB, subgenre_cn="卡牌RPG")
    await set_manual_subgenre("manual_wins", "数字门SLG")

    got = await resolve_subgenres(["has_log", "only_backfill", "manual_wins", "missing"])
    assert got["has_log"] == "国战SLG"
    assert got["only_backfill"] == "基地建设SLG"
    assert got["manual_wins"] == "数字门SLG"
    assert "missing" not in got


@pytest.mark.asyncio
async def test_history_overlays_manual_lock(client):
    """/history 展示层叠加人工锁定（#255 展示半）：行级是 LLM 值、override 写 app_subgenre
    不回写行——不叠加的话锁定后页面仍显 LLM 旧值。锁定行 subgenre_locked=True。"""
    from app.services.app_subgenre import set_manual_subgenre

    await _add_log("battlekiss", "Battle Kiss", SLG_PUB, subgenre_cn="基地建设SLG")
    await _add_log("untouched", "隔壁不动行", SLG_PUB, subgenre_cn="国战SLG")
    await set_manual_subgenre("battlekiss", "数字门SLG", name="Battle Kiss")

    items = (await client.get("/api/newcomers/history", params={"days": 365, "chart": "all"})
             ).json()["items"]
    bk = next(i for i in items if i["app_id"] == "battlekiss")
    other = next(i for i in items if i["app_id"] == "untouched")
    assert bk["subgenre_cn"] == "数字门SLG" and bk["subgenre_locked"] is True
    assert other["subgenre_cn"] == "国战SLG" and other["subgenre_locked"] is False

    # 人工判定「无合适子品类」（None）同样锁定且展示为空
    await set_manual_subgenre("battlekiss", None)
    items = (await client.get("/api/newcomers/history", params={"days": 365, "chart": "all"})
             ).json()["items"]
    bk = next(i for i in items if i["app_id"] == "battlekiss")
    assert bk["subgenre_cn"] is None and bk["subgenre_locked"] is True
