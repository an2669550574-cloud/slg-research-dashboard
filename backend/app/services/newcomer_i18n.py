"""新品中文化（LLM 网关）。

商店描述是源区语言（日/韩/英），团队读中文费劲。LLM 给 is_slg 新品按 app 翻一次：
- summary_cn：一句话「这是什么游戏」（题材+品类+卖点，≤约35字）→ digest 新品行 + 抽屉副标题。
- description_cn：商店描述全文中译 → 抽屉展示、可切原文。

只对 **is_slg** 新品、按 **app_id 去重**（同游戏跨 combo 多行只翻一次、回写全部行）。
走太石网关（OpenAI 兼容）便宜文本模型，cost 经 estimate_cost 记日志。USE_MOCK_DATA /
无 TAISHI_API_KEY → 整体 no-op。每日封顶 NEWCOMER_TRANSLATE_DAILY_CAP 防烧成本。
"""
import json
import logging
import re

from sqlalchemy import select, update

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.newcomer import MarketNewcomerLog
from app.services import llm_gateway

logger = logging.getLogger(__name__)

_PROMPT = """你是手游竞品调研助手。下面是一款游戏的应用商店信息。只输出 JSON（不要解释、不要代码围栏）：
{{"summary": "一句话简体中文，说清这是什么游戏（题材+品类+核心玩法卖点），不超过35字，不带书名号", "translation": "把下面的商店描述完整翻译成简体中文，保留分段，不增删内容"}}

游戏名：{name}
品类：{genre}
商店描述：
{description}"""


def _parse(content: str) -> dict | None:
    """容错解析 LLM 返回 JSON。两层兜底，关键是「截断也别让该行卡在永久重试」：

    1. 从第一个 `{` 起 `raw_decode`：只取首个合法 JSON 值、**忽略后面的脚注/散文**
       （便宜模型偶尔在 JSON 后加「注：…」，贪婪 `\\{.*\\}` 会把脚注的 `}` 也吞进来解析失败）。
    2. raw_decode 失败（多半是 max_tokens 截断了 translation）→ 至少**抢救 summary**
       （它在 JSON 最前、通常完整）。返回 {"summary": ...} 让调用方写回 summary_cn，
       该行从此退出「summary_cn IS NULL」重试集，不再每天空翻烧配额。
    """
    if not content:
        return None
    i = content.find("{")
    if i != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(content[i:])
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            pass
    m = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', content)
    if m:
        try:
            return {"summary": json.loads('"' + m.group(1) + '"')}   # 解转义
        except ValueError:
            return {"summary": m.group(1)}
    return None


async def translate_pending_newcomers(cap: int | None = None) -> int:
    """给未翻译的 is_slg 新品生成 summary_cn + description_cn，返回翻译的 app 数。

    取 description 非空、summary_cn 为空、is_slg 的行，按 app_id 去重，每 app 一次
    LLM 调用，回写该 app **全部** market_newcomer_log 行。USE_MOCK_DATA / 无 key no-op。
    单 app 失败只跳过该 app（summary_cn 留 NULL，下次重试），不拖垮整轮。
    """
    if settings.USE_MOCK_DATA or not settings.TAISHI_API_KEY:
        return 0
    lim = cap if cap is not None else settings.NEWCOMER_TRANSLATE_DAILY_CAP
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(MarketNewcomerLog.app_id, MarketNewcomerLog.name,
                   MarketNewcomerLog.genre, MarketNewcomerLog.description)
            .where(MarketNewcomerLog.is_slg.is_(True),
                   MarketNewcomerLog.description.is_not(None),
                   MarketNewcomerLog.summary_cn.is_(None))
            .order_by(MarketNewcomerLog.id.desc())
        )).all()
    # 按 app_id 去重（同游戏跨 combo 多行只翻一次），保序取最新。
    seen: dict[str, tuple] = {}
    for app_id, name, genre, desc in rows:
        seen.setdefault(app_id, (name, genre, desc))
    if not seen:
        return 0
    client = llm_gateway.get_client()
    model = settings.TAISHI_TEXT_MODEL
    done = 0
    total_usd = 0.0
    for app_id, (name, genre, desc) in list(seen.items())[:lim]:
        prompt = _PROMPT.format(name=name or "", genre=genre or "未知",
                                description=(desc or "")[:1500])
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                # 描述截到 1500 字，全文中译 + 摘要的 CJK 输出可达 2K+ tokens；给足
                # 3500 防截断（截断会让 JSON 不完整、_parse 抢救只剩 summary、丢译文）。
                max_tokens=3500, temperature=0.2,
            )
            content = (resp.choices[0].message.content or "") if resp.choices else ""
            total_usd += llm_gateway.estimate_cost(
                model, llm_gateway.usage_to_dict(getattr(resp, "usage", None))).total_usd
        except Exception:
            logger.warning("newcomer translate failed for %s", app_id, exc_info=True)
            continue
        parsed = _parse(content)
        if not parsed or not str(parsed.get("summary") or "").strip():
            continue
        summary = str(parsed["summary"]).strip()[:200]
        translation = str(parsed.get("translation") or "").strip() or None
        # 回写该 app 全部行（跨 combo/榜）。summary 本就 app 级、country 无关；
        # description_cn 用最新行的译文覆盖全部——同 app 跨国描述偶有差异，但这是竞品
        # 速览、headline 价值在 summary，按 app 翻一次省 LLM 是有意取舍（cost 硬上限
        # NEWCOMER_TRANSLATE_DAILY_CAP × flash 模型 < $0.15/天，不并入 LLM_DAILY_BUDGET）。
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(MarketNewcomerLog)
                .where(MarketNewcomerLog.app_id == app_id)
                .values(summary_cn=summary, description_cn=translation))
            await db.commit()
        done += 1
    if done:
        logger.info("newcomer translate: %d app(s), est $%.4f", done, total_usd)
    return done
