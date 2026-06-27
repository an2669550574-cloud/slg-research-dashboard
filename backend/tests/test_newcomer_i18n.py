"""新品中文化（LLM 网关）：summary_cn / description_cn。

验收：is_slg 新品翻一次写回该 app 全部行 / 按 app 去重（同游戏跨 combo 一次 LLM）/
非 is_slg 不翻 / cap 封顶 / mock + 无 key no-op / digest 新品行带 📝 摘要。中文夹具。
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
async def test_translate_skips_non_slg_and_already_done(app, monkeypatch):
    """非 is_slg 不翻；summary_cn 已有的不重复翻。"""
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.models.newcomer import MarketNewcomerLog
    from app.services import newcomer_i18n as ni
    monkeypatch.setattr(settings, "USE_MOCK_DATA", False)
    monkeypatch.setattr(settings, "TAISHI_API_KEY", "k")
    calls: list = []
    monkeypatch.setattr(ni.llm_gateway, "get_client", lambda: _Client(_FAKE_JSON, calls))

    await _add_log("noise", "麻将游戏", "US", is_slg=False)   # 非 SLG → 跳过
    await _add_log("g2", "无描述", "US", description=None)     # 无描述 → 跳过
    done = await ni.translate_pending_newcomers()
    assert done == 0 and len(calls) == 0
    async with AsyncSessionLocal() as db:
        noise = (await db.execute(select(MarketNewcomerLog).where(
            MarketNewcomerLog.app_id == "noise"))).scalar_one()
    assert noise.summary_cn is None


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


def test_digest_newcomer_line_carries_summary():
    """build_newcomer_lines 把一句话中文摘要拼进新品行（📝）。"""
    from app.services.release_alerts import build_newcomer_lines
    market = {"newcomers": [{"app_id": "g1", "name": "末日喧嚣", "rank": 8,
                             "is_slg": True, "is_reentry": False}]}
    lines = build_newcomer_lines(market, {}, summaries={"g1": "末日生存 SLG，丧尸题材"})
    body = "\n".join(lines)
    assert "末日喧嚣" in body and "📝 末日生存 SLG，丧尸题材" in body
