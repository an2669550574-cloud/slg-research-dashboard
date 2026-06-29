"""新品中文化（LLM 网关）：summary_cn / description_cn。

验收：新品翻一次写回该 app 全部行 / 按 app 去重（同游戏跨 combo 一次 LLM）/
**待识别新厂(is_slg=false)也翻**、忽略名单 / 无描述跳过 / cap 封顶 / mock + 无 key
no-op / digest 新品行带 📝 摘要。中文夹具。
"""
import pytest
from sqlalchemy import select


# ── 假 LLM 网关 client（OpenAI 兼容形状）──────────────────────────────────
class _Msg:
    def __init__(self, content): self.content = content
class _Choice:
    def __init__(self, content): self.message = _Msg(content)
class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = {"prompt_tokens": 120, "completion_tokens": 60}
class _Completions:
    def __init__(self, content, counter): self._c, self._n = content, counter
    async def create(self, **kw):
        self._n.append(1)
        return _Resp(self._c)
class _Chat:
    def __init__(self, content, counter): self.completions = _Completions(content, counter)
class _Client:
    def __init__(self, content, counter): self.chat = _Chat(content, counter)


_FAKE_JSON = '{"summary": "二战题材 SLG，主打真实弹道射击", "translation": "完整的简体中文描述。"}'


async def _add_log(app_id, name, country, is_slg=True, description="raw desc"):
    from app.database import AsyncSessionLocal
    from app.models.newcomer import MarketNewcomerLog
    async with AsyncSessionLocal() as db:
        db.add(MarketNewcomerLog(country=country, platform="ios", app_id=app_id,
                                 as_of="2026-06-27", name=name, is_slg=is_slg,
                                 description=description))
        await db.commit()


@pytest.mark.asyncio
async def test_translate_dedups_by_app_and_writes_all_rows(app, monkeypatch):
    """同游戏跨 combo 多行只调一次 LLM，summary/translation 回写该 app 全部行。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.newcomer import MarketNewcomerLog
    from app.services import newcomer_i18n as ni
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "TAISHI_API_KEY", "k")
    calls: list = []
    monkeypatch.setattr(ni.llm_gateway, "get_client", lambda: _Client(_FAKE_JSON, calls))

    await _add_log("g1", "万国觉醒", "US")
    await _add_log("g1", "万国觉醒", "JP")   # 同 app 第二个 combo
    done = await ni.translate_pending_newcomers()

    assert done == 1 and len(calls) == 1     # 按 app 去重，只调一次 LLM
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(MarketNewcomerLog).where(
            MarketNewcomerLog.app_id == "g1"))).scalars().all()
    assert len(rows) == 2
    assert all("二战题材 SLG" in (r.summary_cn or "") for r in rows)   # 回写全部行
    assert all((r.description_cn or "").startswith("完整") for r in rows)


@pytest.mark.asyncio
async def test_translate_covers_non_slg_but_skips_ignored_and_nodesc(app, monkeypatch):
    """切片2：中文化扩到待识别新厂（is_slg=false 也翻）；忽略名单 / 无描述跳过；
    summary_cn 已有的不重复翻。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.newcomer import MarketNewcomerLog
    from app.models.publisher import PublisherIgnore
    from app.services import newcomer_i18n as ni
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "TAISHI_API_KEY", "k")
    calls: list = []
    monkeypatch.setattr(ni.llm_gateway, "get_client", lambda: _Client(_FAKE_JSON, calls))

    await _add_log("pending", "末日小镇", "US", is_slg=False)   # 待识别新厂 → 现在也翻
    await _add_log("ignored", "麻将游戏", "US", is_slg=False)    # 忽略名单 → 跳过
    await _add_log("nodesc", "无描述", "US", is_slg=False, description=None)  # 无描述 → 跳过
    async with AsyncSessionLocal() as db:
        db.add(PublisherIgnore(kind="app_id", value="ignored", label="麻将游戏"))
        await db.commit()

    done = await ni.translate_pending_newcomers()
    assert done == 1 and len(calls) == 1   # 只翻 pending（ignored / nodesc 跳过）
    async with AsyncSessionLocal() as db:
        pend = (await db.execute(select(MarketNewcomerLog).where(
            MarketNewcomerLog.app_id == "pending"))).scalar_one()
        ign = (await db.execute(select(MarketNewcomerLog).where(
            MarketNewcomerLog.app_id == "ignored"))).scalar_one()
    assert "二战题材 SLG" in (pend.summary_cn or "")   # 待识别新厂也中文化了
    assert ign.summary_cn is None                       # 忽略名单不翻


@pytest.mark.asyncio
async def test_translate_noop_mock_and_no_key(app, monkeypatch):
    """USE_MOCK_DATA / 无 TAISHI_API_KEY → 整体 no-op，不构造 client。"""
    from app.config import settings
    from app.services import newcomer_i18n as ni

    def _boom():
        raise AssertionError("不应构造 client")
    monkeypatch.setattr(ni.llm_gateway, "get_client", _boom)

    monkeypatch.setattr(settings, "USE_MOCK_DATA", True)
    monkeypatch.setattr(settings, "TAISHI_API_KEY", "k")
    assert await ni.translate_pending_newcomers() == 0

    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "TAISHI_API_KEY", None)
    assert await ni.translate_pending_newcomers() == 0


def test_parse_robust_against_footnote_and_truncation():
    """_parse：① JSON 后带脚注（raw_decode 忽略尾部）② 译文被截断（抢救 summary，
    让行退出 NULL 重试集，不每天空翻）。"""
    from app.services.newcomer_i18n import _parse
    # ① 合法 JSON + 尾部散文/脚注（贪婪 {.*} 会失败，raw_decode 不会）
    foot = '{"summary": "末日生存 SLG", "translation": "中文描述。"}\n注：仅供参考{x}'
    assert _parse(foot) == {"summary": "末日生存 SLG", "translation": "中文描述。"}
    # ② max_tokens 截断 translation → JSON 不完整 → 至少抢救出 summary
    trunc = '{"summary": "二战策略游戏", "translation": "很长的中文译文开头但是被截断了'
    got = _parse(trunc)
    assert got and got.get("summary") == "二战策略游戏" and not got.get("translation")
    # ③ 彻底无 JSON → None
    assert _parse("抱歉我无法处理") is None


@pytest.mark.asyncio
async def test_translate_salvages_summary_on_truncation(app, monkeypatch):
    """译文截断时仍写 summary_cn → 该行不再被重选（避免永久重试空翻）。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.newcomer import MarketNewcomerLog
    from app.services import newcomer_i18n as ni
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "TAISHI_API_KEY", "k")
    calls: list = []
    truncated = '{"summary": "丧尸末日 SLG", "translation": "中文译文被截断'
    monkeypatch.setattr(ni.llm_gateway, "get_client", lambda: _Client(truncated, calls))

    await _add_log("g9", "末日喧嚣", "US")
    assert await ni.translate_pending_newcomers() == 1
    async with AsyncSessionLocal() as db:
        r = (await db.execute(select(MarketNewcomerLog).where(
            MarketNewcomerLog.app_id == "g9"))).scalar_one()
    assert r.summary_cn == "丧尸末日 SLG" and r.description_cn is None  # summary 落库，退出重试集
    # 二次：summary_cn 已非空 → 不再选中、不再调 LLM
    assert await ni.translate_pending_newcomers() == 0 and len(calls) == 1


@pytest.mark.asyncio
async def test_translate_writes_valid_subgenre_only(app, monkeypatch):
    """玩法子品类：词表内的值写入 subgenre_cn；LLM 编的非词表值 → None（不脏库、精确匹配不误命中）。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.newcomer import MarketNewcomerLog
    from app.services import newcomer_i18n as ni
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "TAISHI_API_KEY", "k")

    valid = '{"summary": "末日数字门 SLG", "subgenre": "数字门SLG", "translation": "中文。"}'
    monkeypatch.setattr(ni.llm_gateway, "get_client", lambda: _Client(valid, []))
    await _add_log("ssg", "末日先锋", "US")
    assert await ni.translate_pending_newcomers() == 1
    async with AsyncSessionLocal() as db:
        r = (await db.execute(select(MarketNewcomerLog).where(
            MarketNewcomerLog.app_id == "ssg"))).scalar_one()
    assert r.subgenre_cn == "数字门SLG"

    bad = '{"summary": "某策略游戏", "subgenre": "我自己编的子品类", "translation": "中文。"}'
    monkeypatch.setattr(ni.llm_gateway, "get_client", lambda: _Client(bad, []))
    await _add_log("bsg", "怪词游戏", "US")
    assert await ni.translate_pending_newcomers() == 1
    async with AsyncSessionLocal() as db:
        r2 = (await db.execute(select(MarketNewcomerLog).where(
            MarketNewcomerLog.app_id == "bsg"))).scalar_one()
    assert r2.subgenre_cn is None and "某策略游戏" in (r2.summary_cn or "")


def test_digest_newcomer_line_carries_summary():
    """build_newcomer_lines 把一句话中文摘要拼进新品行（📝）。"""
    from app.services.release_alerts import build_newcomer_lines
    market = {"newcomers": [{"app_id": "g1", "name": "末日喧嚣", "rank": 8,
                             "is_slg": True, "is_reentry": False}]}
    lines = build_newcomer_lines(market, {}, summaries={"g1": "末日生存 SLG，丧尸题材"})
    body = "\n".join(lines)
    assert "末日喧嚣" in body and "📝 末日生存 SLG，丧尸题材" in body
